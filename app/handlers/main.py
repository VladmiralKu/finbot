from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from app.database import (
    get_or_create_user, get_categories, add_transaction,
    get_monthly_summary, get_recent_transactions,
    get_category_breakdown, is_premium,
)
from app.keyboards import main_menu, categories_keyboard, confirm_keyboard, premium_keyboard

router = Router()


class AddTransaction(StatesGroup):
    choosing_category = State()
    entering_amount   = State()
    entering_comment  = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    await get_or_create_user(
        user.id,
        user.username or "",
        user.full_name or "",
        user.language_code or "ru",
    )
    await message.answer(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе вести личный бюджет: записывать расходы и доходы, "
        "анализировать траты и давать советы.\n\n"
        "Выбирай действие:",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Выбирай действие:", reply_markup=main_menu())


@router.callback_query(F.data == "add_expense")
async def cb_add_expense(call: CallbackQuery, state: FSMContext):
    categories = await get_categories(call.from_user.id, type_="expense")
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(tx_type="expense")
    await call.message.edit_text(
        "Выбери категорию расхода:",
        reply_markup=categories_keyboard(categories, "cat"),
    )


@router.callback_query(F.data == "add_income")
async def cb_add_income(call: CallbackQuery, state: FSMContext):
    categories = await get_categories(call.from_user.id, type_="income")
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(tx_type="income")
    await call.message.edit_text(
        "Выбери источник дохода:",
        reply_markup=categories_keyboard(categories, "cat"),
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category_selected(call: CallbackQuery, state: FSMContext):
    _, cat_id, kind = call.data.split(":")
    await state.update_data(category_id=int(cat_id), kind=kind)
    await state.set_state(AddTransaction.entering_amount)
    await call.message.edit_text("Введи сумму (например: 350):")


@router.message(AddTransaction.entering_amount)
async def msg_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи корректную сумму, например: 1500")
        return
    await state.update_data(amount=amount)
    await state.set_state(AddTransaction.entering_comment)
    await message.answer(
        "Добавь комментарий (необязательно).\n"
        "Или напиши /skip чтобы пропустить."
    )


@router.message(AddTransaction.entering_comment, Command("skip"))
async def msg_skip_comment(message: Message, state: FSMContext):
    await _save_transaction(message, state, comment="")


@router.message(AddTransaction.entering_comment)
async def msg_comment(message: Message, state: FSMContext):
    await _save_transaction(message, state, comment=message.text or "")


async def _save_transaction(message: Message, state: FSMContext, comment: str):
    data = await state.get_data()
    tx = await add_transaction(
        user_id=message.from_user.id,
        category_id=data["category_id"],
        amount=data["amount"],
        type_=data["tx_type"],
        kind=data["kind"],
        comment=comment,
    )
    await state.clear()
    sign = "−" if data["tx_type"] == "expense" else "+"
    kind_label = {"fixed": "постоянный", "variable": "переменный", "income": "доход"}.get(data["kind"], "")
    await message.answer(
        f"✅ Записано!\n\n"
        f"{sign}{data['amount']:,.0f} ₽  •  {kind_label}\n"
        f"💬 {comment or '—'}",
        reply_markup=confirm_keyboard(tx["id"]),
    )


@router.callback_query(F.data == "report_month")
async def cb_report_month(call: CallbackQuery):
    now = datetime.now()
    summary = await get_monthly_summary(call.from_user.id, now.year, now.month)
    breakdown = await get_category_breakdown(call.from_user.id, now.year, now.month)
    month_name = now.strftime("%B %Y")
    text = (
        f"📊 <b>Отчёт за {month_name}</b>\n\n"
        f"💰 Доходы:             <b>{summary['income']:,.0f} ₽</b>\n"
        f"🔒 Постоянные расходы: <b>{summary['expense_fixed']:,.0f} ₽</b>\n"
        f"🛒 Переменные расходы: <b>{summary['expense_variable']:,.0f} ₽</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{'✅' if summary['balance'] >= 0 else '🔴'} Остаток: "
        f"<b>{summary['balance']:+,.0f} ₽</b>\n\n"
    )
    if breakdown:
        text += "📋 <b>Топ расходов:</b>\n"
        for row in breakdown[:6]:
            icon = "🔒" if row["kind"] == "fixed" else "🛒"
            text += f"  {icon} {row['name']}: {float(row['total']):,.0f} ₽\n"
    prem = await is_premium(call.from_user.id)
    if not prem:
        text += "\n⭐ <i>ИИ-анализ доступен в Premium</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 ИИ-анализ ⭐", callback_data="ai_analyze")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "recent")
async def cb_recent(call: CallbackQuery):
    txs = await get_recent_transactions(call.from_user.id, limit=8)
    if not txs:
        await call.answer("Транзакций пока нет", show_alert=True)
        return
    text = "📋 <b>Последние операции:</b>\n\n"
    for tx in txs:
        sign = "−" if tx["type"] == "expense" else "+"
        date = tx["transaction_date"].strftime("%d.%m")
        text += f"{date}  {sign}{float(tx['amount']):,.0f} ₽  {tx['category_name'] or '—'}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "premium")
async def cb_premium(call: CallbackQuery):
    prem = await is_premium(call.from_user.id)
    text = (
        "⭐ <b>Premium — $3/мес</b>\n\n"
        "Что входит:\n"
        "• Неограниченные транзакции\n"
        "• 🤖 ИИ-анализ расходов\n"
        "• 📷 Сканирование чеков\n"
        "• Расширенные отчёты\n"
        "• Финансовый план на месяц\n"
    )
    if prem:
        text = "✅ <b>У вас активен Premium!</b>\n\n" + text
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=premium_keyboard(prem))


@router.callback_query(F.data == "ai_analyze")
async def cb_ai_analyze(call: CallbackQuery):
    prem = await is_premium(call.from_user.id)
    if not prem:
        await call.answer("Доступно только в Premium ⭐", show_alert=True)
        return
    await call.answer("ИИ-анализ — скоро!", show_alert=True)


@router.callback_query(F.data == "scan_receipt")
async def cb_scan_receipt(call: CallbackQuery):
    prem = await is_premium(call.from_user.id)
    if not prem:
        await call.answer("Доступно только в Premium ⭐", show_alert=True)
        return
    await call.answer("Сканирование чеков — скоро!", show_alert=True)


@router.callback_query(F.data.startswith("delete_tx:"))
async def cb_delete_tx(call: CallbackQuery):
    tx_id = int(call.data.split(":")[1])
    from app.database import get_pool
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM transactions WHERE id=$1 AND user_id=$2",
        tx_id, call.from_user.id,
    )
    await call.message.edit_text("🗑 Транзакция удалена.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=main_menu())


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")]
    ])
    await call.message.edit_text(
        "⚙️ <b>Настройки</b>\n\nУправление категориями — скоро!",
        parse_mode="HTML",
        reply_markup=kb,
    )


# --- Быстрый ввод ---

@router.message(F.text.regexp(r'^[+-]?\d+'))
async def msg_quick_input(message: Message, state: FSMContext):
    """Обрабатывает быстрый ввод транзакций типа -1500 вчера продукты бн"""
    current_state = await state.get_state()
    if current_state:
        return  # Не перехватываем если идёт другой FSM

    from app.parser import parse_quick_input
    parsed = parse_quick_input(message.text)

    if not parsed:
        return

    # Ищем категорию
    categories = await get_categories(message.from_user.id, type_=parsed['type'])
    category_id = None
    kind = 'variable' if parsed['type'] == 'expense' else 'income'

    if parsed['category_hint']:
        for cat in categories:
            if parsed['category_hint'].lower() in cat['name'].lower():
                category_id = cat['id']
                kind = cat['kind']
                break

    if not category_id and categories:
        # Берём первую подходящую категорию
        category_id = categories[0]['id']
        kind = categories[0]['kind']

    if not category_id:
        await message.answer("❌ Не нашёл подходящую категорию.")
        return

    from app.database import add_transaction as _add_tx
    from datetime import datetime

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """INSERT INTO transactions
               (user_id, category_id, amount, type, kind, comment,
                transaction_date, wallet, pnl_period)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            message.from_user.id, category_id, parsed['amount'],
            parsed['type'], kind, parsed['comment'],
            parsed['transaction_date'], parsed['wallet'], parsed['pnl_period']
        )

    sign = "−" if parsed['type'] == 'expense' else "+"
    wallet_names = {'cash': '💵 Нал', 'card': '💳 Безнал', 'other': '🔄 Другое'}
    date_str = parsed['transaction_date'].strftime('%d.%m')

    text = (
        f"✅ <b>Записано!</b>\n\n"
        f"{sign}{parsed['amount']:,.0f} ₽\n"
        f"📅 {date_str}  {wallet_names.get(parsed['wallet'], '')}\n"
    )
    if parsed['comment']:
        text += f"💬 {parsed['comment']}\n"
    if parsed['pnl_period']:
        text += f"📊 ПнЛ: {parsed['pnl_period']}\n"

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data=f"confirm:{tx['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tx:{tx['id']}"),
            ],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ])
    )


# --- Быстрый ввод ---

@router.message(F.text.regexp(r'^[+-]?\d+'))
async def msg_quick_input(message: Message, state: FSMContext):
    """Обрабатывает быстрый ввод транзакций типа -1500 вчера продукты бн"""
    current_state = await state.get_state()
    if current_state:
        return  # Не перехватываем если идёт другой FSM

    from app.parser import parse_quick_input
    parsed = parse_quick_input(message.text)

    if not parsed:
        return

    # Ищем категорию
    categories = await get_categories(message.from_user.id, type_=parsed['type'])
    category_id = None
    kind = 'variable' if parsed['type'] == 'expense' else 'income'

    if parsed['category_hint']:
        for cat in categories:
            if parsed['category_hint'].lower() in cat['name'].lower():
                category_id = cat['id']
                kind = cat['kind']
                break

    if not category_id and categories:
        # Берём первую подходящую категорию
        category_id = categories[0]['id']
        kind = categories[0]['kind']

    if not category_id:
        await message.answer("❌ Не нашёл подходящую категорию.")
        return

    from app.database import add_transaction as _add_tx
    from datetime import datetime

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """INSERT INTO transactions
               (user_id, category_id, amount, type, kind, comment,
                transaction_date, wallet, pnl_period)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            message.from_user.id, category_id, parsed['amount'],
            parsed['type'], kind, parsed['comment'],
            parsed['transaction_date'], parsed['wallet'], parsed['pnl_period']
        )

    sign = "−" if parsed['type'] == 'expense' else "+"
    wallet_names = {'cash': '💵 Нал', 'card': '💳 Безнал', 'other': '🔄 Другое'}
    date_str = parsed['transaction_date'].strftime('%d.%m')

    text = (
        f"✅ <b>Записано!</b>\n\n"
        f"{sign}{parsed['amount']:,.0f} ₽\n"
        f"📅 {date_str}  {wallet_names.get(parsed['wallet'], '')}\n"
    )
    if parsed['comment']:
        text += f"💬 {parsed['comment']}\n"
    if parsed['pnl_period']:
        text += f"📊 ПнЛ: {parsed['pnl_period']}\n"

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data=f"confirm:{tx['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tx:{tx['id']}"),
            ],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ])
    )
