from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from app.database import (
    execute, fetchone, fetchall,
    get_or_create_user, get_categories,
    get_monthly_summary, get_recent_transactions,
    get_category_breakdown, is_premium,
)
from app.keyboards import (
    MAIN_REPLY_KB,
    main_menu,
    manual_input_keyboard,
    categories_keyboard,
    confirm_keyboard,
    premium_keyboard,
)
from app.services.transaction_service import create_transaction
from app.services.insights import build_first_transaction_insight, build_transaction_insight

router = Router()


class AddTransaction(StatesGroup):
    choosing_category = State()
    entering_amount   = State()
    entering_comment  = State()


NO_SPEND_REPLIES = {
    "ничего",
    "ничего не тратил",
    "ничего не тратила",
    "пока ничего",
    "сегодня ничего",
    "нет",
}


def _is_no_spend_reply(text: str) -> bool:
    normalized = " ".join((text or "").lower().strip().split())
    return normalized in NO_SPEND_REPLIES


async def render_categories_text(user_id: int) -> str:
    cats = await get_categories(user_id)
    if not cats:
        return "Категорий пока нет."

    income = [c for c in cats if c.get("type") == "income"]
    expense = [c for c in cats if c.get("type") == "expense"]

    text = "📂 Твои категории\n\n"
    text += "💰 Доходы:\n"
    text += "\n".join(["• " + c["name"] for c in income]) if income else "• пока нет"
    text += "\n\n💸 Расходы:\n"
    text += "\n".join(["• " + c["name"] for c in expense]) if expense else "• пока нет"
    text += (
        "\n\nЧтобы изменить статьи, открой «Ручной ввод» → «Расход» или «Доход» "
        "и напиши: «Заменить Кафе на Рестораны», «Добавить Подарки» или «Удалить Старую статью»."
        "\n\nМожно не разбираться в меню: открой ИИ-помощник и обычными словами попроси настроить категории, "
        "перенести транзакции, создать календарь платежей или поправить старые записи."
    )
    return text


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
        "Привет 👋\n\n"
        "У тебя есть 10 дней пробного Премиума.\n\n"
        "За первые 3 дня я помогу понять, куда уходят деньги: просто записывай траты и доходы текстом или голосом.\n"
        "За неделю мы уже сможем собрать плановый бюджет и найти первые места, где деньги утекают незаметно.\n\n"
        "Не нужно вести таблицы.\n"
        "Просто рассказывай мне, что купил или получил — текстом или голосом.\n\n"
        "Если не хочется разбираться в кнопках, открой ИИ-помощник и скажи обычными словами, что настроить: "
        "категории, транзакции, календарь платежей, заметки или цели.\n\n"
        "Давай попробуем.\n"
        "На что ты сегодня уже потратил деньги?",
        reply_markup=MAIN_REPLY_KB,
    )


@router.message(F.text.in_({"🏠 Меню", "Меню", "/start"}))
async def msg_open_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выбирай действие:", reply_markup=main_menu())


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("Выбирай действие:", reply_markup=main_menu())
    except Exception:
        await call.message.answer("Выбирай действие:", reply_markup=main_menu())


@router.callback_query(F.data == "categories_list")
async def cb_categories_list(call: CallbackQuery):
    await call.message.edit_text(
        await render_categories_text(call.from_user.id),
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]),
    )


@router.callback_query(F.data == "manual_input")
async def cb_manual_input(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Ручной ввод:", reply_markup=manual_input_keyboard())


@router.callback_query(F.data == "add_expense")
async def cb_add_expense(call: CallbackQuery, state: FSMContext):
    from app.database import get_user_tier
    tier = await get_user_tier(call.from_user.id)
    if tier == 'free':
        await call.answer(show_alert=True, text=(
            "Пробный период закончился! Оформи подписку."
        ))
        await call.message.edit_text(
            "Пробный период закончился.\n\nДля записи транзакций нужен тариф Старт или Премиум.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return
    categories = await get_categories(call.from_user.id, type_="expense")
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(tx_type="expense")
    await call.message.edit_text(
        await render_categories_text(call.from_user.id) + "\n\nВыбери категорию расхода:",
        reply_markup=categories_keyboard(categories, "cat", back_callback="manual_input"),
    )


@router.callback_query(F.data == "add_income")
async def cb_add_income(call: CallbackQuery, state: FSMContext):
    from app.database import get_user_tier
    tier = await get_user_tier(call.from_user.id)
    if tier == 'free':
        await call.answer(show_alert=True, text=(
            "Пробный период закончился! Оформи подписку."
        ))
        await call.message.edit_text(
            "Пробный период закончился.\n\nДля записи транзакций нужен тариф Старт или Премиум.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return
    categories = await get_categories(call.from_user.id, type_="income")
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(tx_type="income")
    await call.message.edit_text(
        await render_categories_text(call.from_user.id) + "\n\nВыбери источник дохода:",
        reply_markup=categories_keyboard(categories, "cat", back_callback="manual_input"),
    )


@router.message(AddTransaction.choosing_category, F.text)
async def msg_category_command(message: Message, state: FSMContext):
    from app.services.category_commands import parse_category_command, apply_category_command

    data = await state.get_data()
    scope_type = data.get("tx_type")
    command = await parse_category_command(message.from_user.id, message.text or "", scope_type)
    if not command or command.get("intent") == "unknown":
        await message.answer(
            "Не понял команду. Можно выбрать категорию кнопкой или написать: «замени категорию X на Y».",
        )
        return

    result = await apply_category_command(message.from_user.id, command, scope_type)
    categories = await get_categories(message.from_user.id, type_=scope_type)
    title = "Выбери категорию расхода:" if scope_type == "expense" else "Выбери источник дохода:"
    await message.answer(
        result + "\n\n" + title,
        reply_markup=categories_keyboard(categories, "cat", back_callback="manual_input"),
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
    tx = await create_transaction(
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
    text = (
        f"✅ Записано!\n\n"
        f"{sign}{data['amount']:,.0f} ₽  •  {kind_label}\n"
        f"💬 {comment or '—'}"
    )
    insight = await build_transaction_insight(message.from_user.id, tx["id"])
    if insight:
        text += "\n\n" + insight
    await message.answer(
        text,
        reply_markup=confirm_keyboard(tx["id"]),
    )


@router.callback_query(F.data == "report_month")
async def cb_report_month(call: CallbackQuery):
    now = datetime.now()
    MN = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
          7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    MONTHS_RU = {1:"Yanvar",2:"Fevral",3:"Mart",4:"Aprel",5:"Mai",6:"Iyun",
                 7:"Iyul",8:"Avgust",9:"Sentyabr",10:"Oktyabr",11:"Noyabr",12:"Dekabr"}
    months = []
    for i in range(3):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(
            text=MONTHS_RU[m] + " " + str(y),
            callback_data="report:" + str(y) + ":" + str(m)
        )] for y, m in months],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Отчёт ДДС - выбери месяц:", parse_mode=None, reply_markup=kb)
    return


async def render_month_report(user_id: int, year: int, month: int):
    summary = await get_monthly_summary(user_id, year, month)
    breakdown = await get_category_breakdown(user_id, year, month)
    month_name = str(year) + "-" + str(month).zfill(2)
    text = (
        f"📊 <b>Отчёт за {month_name}</b>\n\n"
        f"💰 Доходы:             <b>{summary['income']:,.0f} ₽</b>\n"
        f"🔒 Постоянные расходы: <b>{summary['expense_fixed']:,.0f} ₽</b>\n"
        f"🛒 Переменные расходы: <b>{summary['expense_variable']:,.0f} ₽</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{'✅' if summary['balance'] >= 0 else '🔴'} Остаток: "
        f"<b>{summary['balance']:+,.0f} ₽</b>\n\n"
    )
    # Все расходные категории с % от доходов (если доходов нет — % от общих расходов)
    income = summary['income']
    total_expense = summary['total_expense']
    pct_base = income if income > 0 else (total_expense if total_expense > 0 else 0)
    balance = summary['balance']
    balance_pct = (balance / pct_base * 100) if pct_base else 0

    # Получаем все категории пользователя
    all_cats = await get_categories(user_id)
    expense_cats = {cat['name']: 0.0 for cat in all_cats if cat.get('type') == 'expense'}

    # Заполняем суммами и kind из breakdown
    expense_kinds = {cat['name']: 'variable' for cat in all_cats if cat.get('type') == 'expense'}
    for row in breakdown:
        name = row['name']
        if name in expense_cats:
            expense_cats[name] = float(row['total'])
            expense_kinds[name] = row['kind']

    # Сортируем по убыванию
    sorted_cats = sorted(expense_cats.items(), key=lambda x: x[1], reverse=True)

    if sorted_cats:
        text += "📋 <b>Расходы по категориям:</b>\n"
        for name, total in sorted_cats:
            pct = (total / pct_base * 100) if pct_base else 0
            icon = "🔒" if expense_kinds.get(name) == 'fixed' else "🛒"
            text += f"  {icon} {name}: {total:,.0f} ₽ ({pct:.0f}%)\n"

    # Добавляем % к остатку
    text = text.replace(
        f"<b>{balance:+,.0f} ₽</b>",
        f"<b>{balance:+,.0f} ₽ ({balance_pct:.0f}%)</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 График", callback_data="chart:" + str(year) + ":" + str(month))],
        [InlineKeyboardButton(text="🤖 ИИ-помощник", callback_data="ai_assistant")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("report:"))
async def cb_report_by_month(call: CallbackQuery):
    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    text, kb = await render_month_report(call.from_user.id, year, month)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "recent")
async def cb_recent(call: CallbackQuery):
    now = datetime.now()
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}
    months = []
    for i in range(3):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(
            text=f"{MONTHS[m]} {y}",
            callback_data=f"txlist:{y}:{m}"
        )] for y, m in months],
        [InlineKeyboardButton(text="Выгрузить всё в Excel", callback_data="export_all")],
        [InlineKeyboardButton(text="Удалить транзакцию", callback_data="delete_by_id")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Транзакции — выбери месяц:", parse_mode=None, reply_markup=kb)



@router.callback_query(F.data == "ai_analyze")
async def cb_ai_analyze(call: CallbackQuery):
    prem = await is_premium(call.from_user.id)
    if not prem:
        await call.answer("Доступно только в Premium ⭐", show_alert=True)
        return
    await call.answer("ИИ-помощник уже доступен в меню.", show_alert=True)


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
    await execute(
        "DELETE FROM transactions WHERE id=%s AND user_id=%s",
        (tx_id, call.from_user.id)
    )
    await call.message.edit_text("Транзакция удалена.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("confirm_delete_intent:"))
async def cb_confirm_delete_intent(call: CallbackQuery):
    from app.database import delete_transaction_by_id

    tx_id = int(call.data.split(":")[1])
    success = await delete_transaction_by_id(call.from_user.id, tx_id)
    text = f"Транзакция #{tx_id} удалена." if success else "Транзакция не найдена."
    await call.message.edit_text(text, reply_markup=main_menu())


@router.callback_query(F.data == "cancel_delete_intent")
async def cb_cancel_delete_intent(call: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteTxState.waiting_id)
    await call.message.edit_text(
        "Хорошо. Введите номер транзакции для удаления, например: 42.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]),
    )


@router.callback_query(F.data.startswith("confirm_change_tx_category:"))
async def cb_confirm_change_tx_category(call: CallbackQuery):
    _, tx_id, category_id = call.data.split(":")
    row = await fetchone(
        "SELECT name, type, kind FROM categories WHERE id=%s AND user_id=%s",
        (int(category_id), call.from_user.id),
    )
    if not row:
        await call.message.edit_text("Категория не найдена.", reply_markup=main_menu())
        return
    category_name, type_, kind = row
    await execute(
        "UPDATE transactions SET category_id=%s, type=%s, kind=%s WHERE id=%s AND user_id=%s",
        (int(category_id), type_, kind, int(tx_id), call.from_user.id),
    )
    await call.message.edit_text(
        f"Готово. Транзакция #{tx_id} перенесена в категорию «{category_name}».",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "cancel_change_tx_category")
async def cb_cancel_change_tx_category(call: CallbackQuery):
    await call.message.edit_text(
        "Смена категории отменена. Можно написать номер транзакции и новую категорию точнее.",
        reply_markup=main_menu(),
    )


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

@router.message(F.text.regexp(r'^[+-]?\d+(?![.\d])'), StateFilter(default_state))
async def msg_quick_input(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        return

    from app.services.transaction_ai import extract_transactions_from_text

    transactions = await extract_transactions_from_text(message.from_user.id, message.text or "", source="text")
    if not transactions:
        return

    tx = transactions[0]
    saved = await create_transaction(
        user_id=message.from_user.id,
        category_id=tx["category_id"],
        amount=tx["amount"],
        type_=tx["type"],
        kind=tx.get("kind"),
        comment=tx.get("comment") or "",
        transaction_date=tx.get("transaction_date"),
        pnl_period=tx.get("pnl_period"),
    )

    sign = "−" if tx["type"] == "expense" else "+"
    date_str = tx["transaction_date"].strftime("%d.%m")

    text = (
        f"✅ <b>Записано!</b>\n\n"
        f"{sign}{tx['amount']:,.0f} ₽\n"
        f"📂 {tx['category_name']}\n"
        f"📅 {date_str}\n"
    )
    if tx.get("comment"):
        text += f"💬 {tx['comment']}\n"
    if tx.get("pnl_period"):
        text += f"📊 ПнЛ: {tx['pnl_period']}\n"

    insight = await build_transaction_insight(message.from_user.id, saved["id"])
    if insight:
        text += "\n" + insight + "\n"

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data=f"confirm:{saved['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tx:{saved['id']}"),
            ],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ])
    )


# --- /reset и /deleteaccount ---

@router.message(Command("reset"))
async def cmd_reset(message: Message):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        "⚠️ <b>Сброс данных</b>\n\n"
        "Это удалит все твои транзакции и регулярные платежи.\n"
        "Категории и настройки сохранятся.\n\n"
        "Ты уверен?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить данные", callback_data="confirm_reset"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu"),
            ]
        ])
    )


@router.message(Command("deleteaccount"))
async def cmd_delete_account(message: Message):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        "🗑 <b>Удаление аккаунта</b>\n\n"
        "Это удалит ВСЕ твои данные без возможности восстановления:\n"
        "транзакции, категории, настройки, подписку.\n\n"
        "При следующем /start ты начнёшь заново.\n\n"
        "Ты уверен?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить аккаунт", callback_data="confirm_delete_account"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu"),
            ]
        ])
    )


@router.callback_query(F.data == "confirm_reset")
async def cb_confirm_reset(call: CallbackQuery):
    await execute(
        "DELETE FROM transactions WHERE user_id=%s",
        (call.from_user.id,)
    )
    await execute(
        "DELETE FROM recurring_payments WHERE user_id=%s",
        (call.from_user.id,)
    )
    await call.message.edit_text(
        "✅ Все транзакции и регулярные платежи удалены.\n\n"
        "Можешь начинать заново!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
        ])
    )


@router.callback_query(F.data == "confirm_delete_account")
async def cb_confirm_delete_account(call: CallbackQuery):
    await execute(
        "DELETE FROM users WHERE id=%s",
        (call.from_user.id,)
    )
    await call.message.edit_text(
        "🗑 Аккаунт удалён. Все данные стёрты.\n\n"
        "Нажми /start чтобы начать заново."
    )


# --- Дашборд управленца ---

@router.callback_query(F.data == "dashboard")
async def cb_dashboard(call: CallbackQuery):
    from app.database import get_dashboard, can_use_feature
    if not await can_use_feature(call.from_user.id, 'business_tools'):
        await call.message.edit_text(
            "Табло управленца доступно на тарифе Business.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    now = datetime.now()
    d = await get_dashboard(call.from_user.id, now.year, now.month)

    MONTHS = {1:'Январь',2:'Февраль',3:'Март',4:'Апрель',5:'Май',6:'Июнь',
              7:'Июль',8:'Август',9:'Сентябрь',10:'Октябрь',11:'Ноябрь',12:'Декабрь'}

    # Динамика
    if d['dynamics'] is not None:
        dyn_icon = "📈" if d['dynamics'] >= 0 else "📉"
        dyn_str = f"{dyn_icon} {d['dynamics']:+.1f}% vs прошлый месяц"
    else:
        dyn_str = "Нет данных за прошлый месяц"

    # Ближайшие платежи
    upcoming_str = ""
    if d['upcoming']:
        upcoming_str = "\n\nБлижайшие платежи (7 дней):\n"
        for p in d['upcoming']:
            upcoming_str += f"  {p[2].strftime('%d.%m')} {p[0]} — {float(p[1]):,.0f} руб.\n"

    # Топ расходов
    top_cats = sorted([c for c in d['categories'] if c[1] in ('fixed','variable')],
                      key=lambda x: x[2], reverse=True)[:3]
    top_str = ""
    if top_cats:
        top_str = "\nТоп расходов:\n"
        for name, kind, total in top_cats:
            pct = total / d['income'] * 100 if d['income'] > 0 else 0
            top_str += f"  {name}: {total:,.0f} ({pct:.1f}%)\n"

    text = (
        f"Табло управленца — {MONTHS[now.month]} {now.year}\n\n"
        f"Выручка: {d['income']:,.0f} руб.\n"
        f"Прямые расходы: -{d['variable_expense']:,.0f} руб.\n"
        f"Косвенные расходы: -{d['fixed_expense']:,.0f} руб.\n\n"
        f"EBITDA: {d['ebitda']:,.0f} руб."
    )

    if d['depreciation'] > 0:
        text += f"\n  Амортизация: -{d['depreciation']:,.0f}"
    if d['tax'] > 0:
        text += f"\n  Налоги: -{d['tax']:,.0f}"
    if d['loan_body'] > 0 or d['loan_pct'] > 0:
        text += f"\n  Кредиты: -{d['loan_body']+d['loan_pct']:,.0f}"

    net_icon = "✅" if d['net_profit'] >= 0 else "🔴"
    text += (
        f"\n\n{net_icon} Чистая прибыль: {d['net_profit']:,.0f} руб. ({d['net_profit_pct']:.1f}%)\n"
        f"{dyn_str}\n"
        f"\nТранзакций: {d['tx_count']}"
        f"{top_str}"
        f"{upcoming_str}"
    )

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


# --- Транзакции по месяцу ---

@router.callback_query(F.data.startswith("txlist:"))
async def cb_txlist(call: CallbackQuery):
    from app.database import get_transactions_by_month
    await call.answer()
    parts = call.data.split(":")
    _, year, month = parts[:3]
    year, month = int(year), int(month)
    offset = int(parts[3]) if len(parts) > 3 else 0
    txs = await get_transactions_by_month(call.from_user.id, year, month)

    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    if not txs:
        await call.message.edit_text(
            f"За {MONTHS[month]} {year} транзакций нет.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="recent")]
            ])
        )
        return

    page_size = 30
    page = txs[offset:offset + page_size]
    text = f"Транзакции за {MONTHS[month]} {year}\n"
    text += f"{offset + 1}-{min(offset + page_size, len(txs))} из {len(txs)}\n\n"
    for tx in page:
        tx_id, date, amount, type_, comment, cat_name, wallet = tx
        sign = "-" if type_ == "expense" else "+"
        comment_str = f" | {comment}" if comment else ""
        line = f"#{tx_id} {date.strftime('%d.%m')} {sign}{abs(float(amount)):,.0f} {cat_name or ''}{comment_str}\n"
        if len(text) + len(line) > 3500:
            break
        text += line

    buttons = []
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"txlist:{year}:{month}:{max(0, offset-page_size)}"))
    if offset + page_size < len(txs):
        nav.append(InlineKeyboardButton(text="Ещё ▶", callback_data=f"txlist:{year}:{month}:{offset+page_size}"))
    if nav:
        buttons.append(nav)
    buttons.extend([
        [InlineKeyboardButton(text="✏️ Изменить транзакцию", callback_data=f"edit_by_id:{year}:{month}")],
        [InlineKeyboardButton(text="🗑 Удалить транзакцию", callback_data="delete_by_id")],
        [InlineKeyboardButton(text="Назад", callback_data="recent")],
    ])

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# --- Редактирование транзакции ---

class EditTxState(StatesGroup):
    waiting_value = State()


@router.callback_query(F.data.startswith("edit_by_id:"))
async def cb_edit_by_id(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    offset = int(parts[3]) if len(parts) > 3 else 0
    await show_edit_list(call, year, month, offset)


async def show_edit_list(call, year, month, offset=0):
    from app.database import get_transactions_by_month
    txs = await get_transactions_by_month(call.from_user.id, year, month)
    if not txs:
        await call.answer("Нет транзакций за этот месяц.")
        return

    page = txs[offset:offset+20]
    buttons = []
    for tx in page:
        tx_id, date, amount, type_, comment, cat_name, wallet = tx
        sign = "-" if type_ == "expense" else "+"
        label = f"#{tx_id} {date.strftime('%d.%m')} {sign}{int(float(amount))} {cat_name or ''}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"edit_pick:{tx_id}:{year}:{month}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"edit_by_id:{year}:{month}:{offset-20}"))
    if offset + 20 < len(txs):
        nav.append(InlineKeyboardButton(text="Ещё ▶", callback_data=f"edit_by_id:{year}:{month}:{offset+20}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="Отмена", callback_data=f"txlist:{year}:{month}")])
    await call.message.edit_text(
        f"Выбери транзакцию ({offset+1}-{min(offset+20, len(txs))} из {len(txs)}):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("edit_pick:"))
async def cb_edit_pick(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    tx_id, year, month = int(parts[1]), parts[2], parts[3]

    tx = await fetchone(
        "SELECT t.id, t.amount, t.type, t.comment, t.transaction_date, c.name as cat_name "
        "FROM transactions t LEFT JOIN categories c ON t.category_id = c.id "
        "WHERE t.id = %s AND t.user_id = %s",
        (tx_id, call.from_user.id)
    )
    if not tx:
        await call.answer("Транзакция не найдена.")
        return

    await state.update_data(tx_id=tx_id, year=year, month=month)
    await state.set_state(EditTxState.waiting_value)

    # fetchone возвращает dict или tuple — обрабатываем оба
    if isinstance(tx, dict):
        tx_type = tx.get('type', 'expense')
        tx_amount = tx.get('amount', 0)
        tx_cat = tx.get('cat_name', '')
        tx_comment = tx.get('comment', '')
        tx_date = tx.get('transaction_date', '')
    else:
        tx_type = tx[2]
        tx_amount = tx[1]
        tx_cat = tx[5] if len(tx) > 5 else ''
        tx_comment = tx[3] if len(tx) > 3 else ''
        tx_date = tx[4] if len(tx) > 4 else ''

    sign = "-" if tx_type == 'expense' else "+"
    current = (sign + str(int(float(tx_amount))) + " " +
               str(tx_cat or '') + " " +
               str(tx_comment or ''))

    await call.message.edit_text(
        "Текущая транзакция: " + current.strip() + chr(10) + chr(10) +
        "Введи новые данные в формате:" + chr(10) +
        "ДД.ММ -500 Категория комментарий" + chr(10) + chr(10) +
        "Пример: 13.06 -500 Еда кофе с молоком",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"txlist:{year}:{month}")]
        ])
    )


@router.message(EditTxState.waiting_value, F.text)
async def msg_edit_tx_value(message: Message, state: FSMContext):
    from app.database import get_categories
    from app.parser import parse_quick_input
    data = await state.get_data()
    tx_id = data['tx_id']
    year = data.get('year', '')
    month = data.get('month', '')

    parsed = parse_quick_input(message.text)
    if not parsed or not parsed.get('amount'):
        await message.answer("Не удалось распознать. Попробуй формат: 13.06 -500 Еда кофе")
        return

    amount = parsed['amount']
    type_ = parsed['type']
    date = parsed.get('transaction_date')
    hint = parsed.get('category_hint', '')
    comment = parsed.get('comment', '')

    cats = await get_categories(message.from_user.id)
    category_id = None
    matched_cat = None
    for cat in cats:
        if hint and (hint.lower().strip() in cat['name'].lower() or cat['name'].lower() in hint.lower().strip()):
            category_id = cat['id']
            matched_cat = cat
            break
    # Если категория найдена — берём тип из неё
    if matched_cat:
        type_ = matched_cat.get('type', type_)
    if not category_id:
        for cat in cats:
            if 'прочие' in cat['name'].lower() and cat.get('type') == type_:
                category_id = cat['id']
                break
    if not category_id:
        for cat in cats:
            if cat.get('type') == type_:
                category_id = cat['id']
                break

    try:
        await execute(
            "UPDATE transactions SET amount=%s, type=%s, category_id=%s, comment=%s, transaction_date=%s WHERE id=%s AND user_id=%s",
            (amount, type_, category_id, comment, date, tx_id, message.from_user.id)
        )
        await state.clear()
        await message.answer(
            "Транзакция обновлена.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад к транзакциям", callback_data=f"txlist:{year}:{month}")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
    except Exception as e:
        await state.clear()
        await message.answer("Ошибка: " + str(e))


# --- Удаление по номеру ---

class DeleteTxState(StatesGroup):
    waiting_id = State()


@router.callback_query(F.data == "delete_by_id")
async def cb_delete_by_id(call: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteTxState.waiting_id)
    await call.message.answer(
        "Напишите номер транзакции для удаления (например: 42):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="recent")]
        ])
    )


@router.message(DeleteTxState.waiting_id, F.text)
async def msg_delete_tx_by_id(message: Message, state: FSMContext):
    from app.database import delete_transaction_by_id
    try:
        tx_id = int(message.text.strip().replace("#", ""))
    except ValueError:
        handled = await handle_intent_message(message, state, message.text, source="text")
        if not handled:
            await message.answer("Введи числовой номер транзакции, например: 42")
        return

    await state.clear()
    success = await delete_transaction_by_id(message.from_user.id, tx_id)
    if success:
        await message.answer(
            f"Транзакция #{tx_id} удалена.",
            reply_markup=main_menu()
        )
    else:
        await message.answer(
            f"Транзакция #{tx_id} не найдена или не принадлежит тебе.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Попробовать снова", callback_data="delete_by_id")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )


@router.message(DeleteTxState.waiting_id, F.voice)
async def msg_delete_tx_by_voice(message: Message, state: FSMContext):
    from app.handlers.voice import transcribe_voice

    thinking = await message.answer("Распознаю голос...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        text = await transcribe_voice(file_bytes.read())
        await thinking.delete()
    except Exception as e:
        await thinking.delete()
        await message.answer("Не удалось распознать голос: " + str(e))
        return

    if not text:
        await message.answer("Не удалось распознать голос. Попробуй ещё раз.")
        return

    await message.answer("Распознано: " + text)
    if text.strip().replace("#", "").isdigit():
        await state.clear()
        from app.database import delete_transaction_by_id
        tx_id = int(text.strip().replace("#", ""))
        success = await delete_transaction_by_id(message.from_user.id, tx_id)
        await message.answer(
            f"Транзакция #{tx_id} удалена." if success else f"Транзакция #{tx_id} не найдена или не принадлежит тебе.",
            reply_markup=main_menu() if success else InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Попробовать снова", callback_data="delete_by_id")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    handled = await handle_intent_message(message, state, text, source="voice")
    if not handled:
        await message.answer("Не понял, какую транзакцию удалить. Можно сказать номер или фразу вроде «удали 45 рублей за проезд».")


# --- Экспорт в Excel ---

@router.callback_query(F.data == "export_excel")
async def cb_export_excel(call: CallbackQuery):
    from app.database import get_all_transactions_for_export
    import io
    try:
        import openpyxl
    except ImportError:
        await call.answer("openpyxl не установлен", show_alert=True)
        return

    txs = await get_all_transactions_for_export(call.from_user.id)
    if not txs:
        await call.answer("Транзакций нет", show_alert=True)
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Транзакции"
    ws.append(["#", "Дата", "Сумма", "Тип", "Категория", "Комментарий", "ПнЛ период"])

    for tx in txs:
        tx_id, date, amount, type_, cat, comment, pnl = tx
        ws.append([
            tx_id,
            date.strftime("%d.%m.%Y"),
            float(amount) if type_ == "income" else -float(amount),
            "Доход" if type_ == "income" else "Расход",
            cat or "",
            comment or "",
            pnl or "",
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from aiogram.types import BufferedInputFile
    await call.message.answer_document(
        BufferedInputFile(buf.read(), filename="transactions.xlsx"),
        caption="Все транзакции"
    )
    await call.answer()


# --- ПнЛ отчёт ---

@router.callback_query(F.data == "pnl_report")
async def cb_pnl_report(call: CallbackQuery):
    from app.database import get_pnl_report, can_use_feature
    if not await can_use_feature(call.from_user.id, 'pnl_table'):
        await call.message.edit_text(
            "ПнЛ отчёт доступен на тарифе Premium и выше.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    now = datetime.now()
    d = await get_pnl_report(call.from_user.id, now.year, now.month)

    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    pct = d['pct']

    text = f"ПнЛ — {MONTHS[now.month]} {now.year}\n\n"

    # Выручка
    text += f"ВЫРУЧКА: {d['income']:,.0f} руб.\n"
    for name, total in d['income_cats']:
        text += f"  {name}: {total:,.0f} ({pct(total)})\n"

    # Прямые (переменные)
    text += f"\nПЕРЕМЕННЫЕ РАСХОДЫ: -{d['variable']:,.0f} ({pct(d['variable'])})\n"
    for name, total in d['variable_cats']:
        text += f"  {name}: -{total:,.0f} ({pct(total)})\n"

    # Валовая прибыль
    gp_icon = "+" if d['gross_profit'] >= 0 else ""
    text += f"\nМаржинальная прибыль: {gp_icon}{d['gross_profit']:,.0f} ({pct(d['gross_profit'])})\n"

    # Косвенные (постоянные)
    text += f"\nПОСТОЯННЫЕ РАСХОДЫ: -{d['fixed']:,.0f} ({pct(d['fixed'])})\n"
    for name, total in d['fixed_cats']:
        text += f"  {name}: -{total:,.0f} ({pct(total)})\n"

    # EBITDA
    eb_icon = "+" if d['ebitda'] >= 0 else ""
    text += f"\nEBITDA: {eb_icon}{d['ebitda']:,.0f} ({pct(d['ebitda'])})\n"

    # Ниже EBITDA
    if d['depreciation'] > 0:
        text += f"  Амортизация: -{d['depreciation']:,.0f}\n"
    if d['tax'] > 0:
        text += f"  Налоги: -{d['tax']:,.0f}\n"
    if d['loan_body'] > 0:
        text += f"  Кредит (тело): -{d['loan_body']:,.0f}\n"
    if d['loan_pct'] > 0:
        text += f"  Кредит (проценты): -{d['loan_pct']:,.0f}\n"

    # ЧП
    np_icon = "+" if d['net_profit'] >= 0 else ""
    text += f"\nЧИСТАЯ ПРИБЫЛЬ: {np_icon}{d['net_profit']:,.0f} ({pct(d['net_profit'])})\n"

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


# --- График ---

@router.callback_query(F.data == "chart_month")
async def cb_chart_month(call: CallbackQuery):
    from app.charts import generate_monthly_chart
    from aiogram.types import BufferedInputFile
    now = datetime.now()
    await call.answer("Генерирую график...")
    try:
        img = await generate_monthly_chart(call.from_user.id, now.year, now.month)
        await call.message.answer_photo(
            BufferedInputFile(img, filename="chart.png"),
            caption="График за " + str(now.month) + "/" + str(now.year),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
    except Exception as e:
        await call.message.answer("Ошибка генерации графика: " + str(e))


# --- Меню отчётов ---

@router.callback_query(F.data == "reports_menu")
async def cb_reports_menu(call: CallbackQuery):
    from app.keyboards import reports_menu_kb
    await call.message.edit_text(
        "Отчёты и инструменты:",
        parse_mode=None,
        reply_markup=reports_menu_kb()
    )


@router.callback_query(F.data.startswith("chart:"))
async def cb_chart_by_month(call: CallbackQuery):
    from app.charts import generate_monthly_chart
    from aiogram.types import BufferedInputFile
    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}
    await call.answer("Генерирую график...")
    try:
        img = await generate_monthly_chart(call.from_user.id, year, month)
        await call.message.answer_photo(
            BufferedInputFile(img, filename="chart.png"),
            caption="График за " + MONTHS[month] + " " + str(year),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
    except Exception as e:
        await call.message.answer("Ошибка: " + str(e))


@router.callback_query(F.data == "export_all")
async def cb_export_all(call: CallbackQuery):
    from app.database import fetchall
    import openpyxl
    from io import BytesIO
    from aiogram.types import BufferedInputFile

    await call.answer("Готовлю выгрузку...")

    wb = openpyxl.Workbook()

    # 1. Транзакции
    ws1 = wb.active
    ws1.title = "Транзакции"
    ws1.append(["ID", "Сумма", "Тип", "Вид", "Категория", "Комментарий", "Дата", "Создано"])
    rows = await fetchall("""
        SELECT t.id, t.amount, t.type, t.kind, c.name as category,
               t.comment, t.transaction_date, t.created_at
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = %s
        ORDER BY t.transaction_date DESC
    """, (call.from_user.id,))
    for r in rows:
        ws1.append([r[0], float(r[1]), r[2], r[3],
                    r[4], r[5], str(r[6]), str(r[7])])

    # 2. Категории
    ws2 = wb.create_sheet("Категории")
    ws2.append(["ID", "Название", "Тип", "Вид"])
    cats = await fetchall("SELECT id, name, type, kind FROM categories WHERE user_id = %s", (call.from_user.id,))
    for r in cats:
        ws2.append([r[0], r[1], r[2], r[3]])

    # 3. Заметки
    ws3 = wb.create_sheet("Заметки")
    ws3.append(["ID", "Текст", "Создано"])
    notes = await fetchall("SELECT id, text, created_at FROM notes WHERE user_id = %s ORDER BY created_at DESC", (call.from_user.id,))
    for r in notes:
        ws3.append([r[0], r[1], str(r[2])])

    # 4. Цели
    ws4 = wb.create_sheet("Цели")
    ws4.append(["ID", "Текст", "Создано"])
    goals = await fetchall("SELECT id, goal_text, created_at FROM user_goals WHERE user_id = %s ORDER BY created_at DESC", (call.from_user.id,))
    for r in goals:
        ws4.append([r[0], r[1], str(r[2])])

    # 5. История ИИ
    try:
        ws5 = wb.create_sheet("Диалоги ИИ")
        ws5.append(["ID", "Роль", "Сообщение", "Дата"])
        history = await fetchall("SELECT id, role, content, created_at FROM ai_history WHERE user_id = %s ORDER BY created_at", (call.from_user.id,))
        for r in history:
            ws5.append([r[0], r[1], r[2], str(r[3])])
    except Exception:
        pass

    # Сохраняем
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    await call.message.answer_document(
        BufferedInputFile(buf.read(), filename="export_" + str(call.from_user.id) + ".xlsx"),
        caption="Полная выгрузка базы данных",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )



HELP_TRIGGERS = [
    "помощь", "помоги", "не понимаю", "как пользоваться", "что умеешь",
    "как работает", "объясни", "расскажи", "хелп", "help", "мне нужна помощь",
    "что ты умеешь", "как добавить", "как записать", "как посмотреть",
    "не знаю", "подскажи", "инструкция"
]

SUPPORT_EMAIL = "findirvladislavku@gmail.com"
SUPPORT_CHANNEL = "@findir43"

BOT_KNOWLEDGE = """
Ты — встроенный помощник финансового бота "Баланс". Отвечай кратко, дружелюбно, с emoji.
Вот что умеет бот:

📥 РАСХОДЫ И ДОХОДЫ
- Кнопка "Ручной ввод" → выбери расход или доход → выбери категорию → введи сумму → опционально комментарий
- Быстрый ввод текстом: просто напиши сумму, например "500" или "-500 кофе"
- Голосом можно вносить операции из любого места бота: "кофе 250, такси 700", "получил зарплату 50000"
- Важно: в комментарии к голосовой операции лучше не говорить числа, количество и номера. Вместо "купил 2 кофе по 250" скажи "кофе 500", иначе бот может принять числа за отдельные операции

📋 ПОСЛЕДНИЕ ТРАНЗАКЦИИ
- Кнопка "Последние" → список последних операций
- Можно редактировать или удалять каждую транзакцию

📊 ОТЧЁТЫ
- Кнопка "Отчёты" → календарь, отчёт ДДС, графики по месяцам
- Выгрузка всей базы в Excel (на тарифе Премиум)

🤖 ИИ-ПОМОЩНИК
- Кнопка "ИИ-помощник" → финансовый советник на базе GPT-4o
- Можно обсудить бюджет, финансовый план, сложную ситуацию, долги, траты и доходы
- Можно установить или удалить финансовую цель
- На тарифе Премиум можно общаться вообще на любые темы, не только финансовые
- Голосовые сообщения работают внутри ассистента (кроме тарифа Скан и текст)

🧾 СКАНИРОВАНИЕ ЧЕКОВ
- Просто отправь фото чека → бот автоматически распознает итоговую сумму

📝 ЗАМЕТКИ
- Кнопка "Заметки" → текстовые заметки, не привязанные к транзакциям

🔁 ПОСТОЯННЫЕ РАСХОДЫ
- Настрой регулярные платежи (аренда, подписки)
- В день платежа бот напомнит и спросит оплачено ли

📂 УПРАВЛЕНИЕ КАТЕГОРИЯМИ
- Чтобы заменить расходную статью: открой "Ручной ввод" → "Расход" и напиши "Заменить старую статью на новую"
- Чтобы заменить доходную статью: открой "Ручной ввод" → "Доход" и напиши "Заменить старую статью на новую"
- Там же можно написать "Добавить название статьи" или "Удалить название статьи"
- Эти команды можно сказать голосом, находясь внутри расходов или доходов

⭐ ТАРИФЫ
- Бесплатно: только просмотр последних и отчётов
- Скан и текст (149 руб/мес): текстовый ввод, чеки, 60 ИИ-сообщений, без голоса
- База (290 руб/мес): + голосовой ввод, 150 ИИ-сообщений
- Премиум (800 руб/мес): + безлимитный ИИ, свободные беседы на любые темы, Excel

📌 КОМАНДЫ
- /start — главное меню
- /help — эта справка
- /reset — удалить все транзакции
- /deleteaccount — удалить аккаунт полностью

❓ Если что-то непонятно — просто спроси меня! По сложным вопросам и проблемам пиши на """ + SUPPORT_EMAIL + """
"""


@router.message(Command("offer"))
async def cmd_offer(message: Message):
    from app.offer_text import OFFER_TEXT, OFFER_TEXT_2, OFFER_TEXT_3, OFFER_TEXT_4, OFFER_TEXT_5
    for part in [OFFER_TEXT, OFFER_TEXT_2, OFFER_TEXT_3, OFFER_TEXT_4, OFFER_TEXT_5]:
        await message.answer(part, parse_mode=None)
    await message.answer(
        "Меню",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📌 Основные функции бота «Баланс»\n\n"
        "✍️ Ручной ввод — записать трату или поступление (кнопкой, текстом или голосом)\n"
        "📋 Последние — история операций, можно редактировать и удалять\n"
        "📊 Отчёты — календарь, отчёт ДДС, графики, выгрузка в Excel (Премиум)\n"
        "🤖 ИИ-помощник — бюджет, план, финансовые вопросы, цели; на Премиум — свободные беседы на любые темы\n"
        "🧾 Чеки — отправь фото чека, сумма распознается автоматически\n"
        "📝 Заметки — текстовые заметки, не привязанные к транзакциям\n"
        "🔁 Постоянные расходы — регулярные платежи с напоминаниями\n\n"
        "🎙 Голосовой ввод\n"
        "Операции можно вносить голосом из любого места бота.\n"
        "Примеры: \"кофе 250, такси 700\", \"получил зарплату 50000\", \"продукты 3200 и аренда 30000\".\n"
        "Важно: в комментарии не указывай числа, количество и номера. Лучше сказать \"кофе 500\", а не \"2 кофе по 250\", чтобы бот не принял числа за отдельные операции.\n\n"
        "📂 Категории\n"
        "Чтобы изменить расходную статью: открой \"Ручной ввод\" → \"Расход\".\n"
        "Чтобы изменить доходную статью: открой \"Ручной ввод\" → \"Доход\".\n"
        "Внутри нужного раздела можно написать или сказать голосом:\n"
        "— \"Заменить Кафе на Рестораны\"\n"
        "— \"Добавить Подарки\"\n"
        "— \"Удалить Старую статью\"\n\n"
        "🤖 ИИ-помощник\n"
        "В ИИ-помощнике можно обсудить бюджет, финансовый план, проблемы с расходами, идеи экономии, а также установить или удалить финансовую цель.\n\n"
        "⭐ Тарифы\n"
        "Скан и текст — 149 руб/мес, текст + чеки, 60 ИИ-сообщений\n"
        "База — 290 руб/мес, + голос, 150 ИИ-сообщений\n"
        "Премиум — 800 руб/мес, безлимитный ИИ + свободные беседы + Excel\n\n"
        "📌 Команды\n"
        "/start — главное меню\n"
        "/help — эта справка\n"
        "/offer — публичная оферта (условия использования)\n"
        "/reset — удалить все транзакции\n"
        "/deleteaccount — удалить аккаунт полностью\n\n"
        "✉️ Связь и поддержка\n"
        "Если возникла проблема или есть вопрос — пиши на " + SUPPORT_EMAIL + "\n"
        "Канал с обучающими материалами: " + SUPPORT_CHANNEL
    )
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ]))


async def answer_help_question(user_text: str, bot_answer_func=None):
    """Отвечает на вопрос о работе бота через GPT"""
    import httpx, os
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 400,
                "messages": [
                    {"role": "system", "content": BOT_KNOWLEDGE},
                    {"role": "user", "content": user_text}
                ],
            },
            timeout=15.0
        )
        data = response.json()
        return data["choices"][0]["message"]["content"]


def _format_tx_preview(tx) -> str:
    tx_id, tx_date, amount, type_, comment, category = tx
    sign = "-" if type_ == "expense" else "+"
    comment_part = f" {comment}" if comment else ""
    return f"#{tx_id} {tx_date.strftime('%d.%m')} {sign}{abs(float(amount)):,.0f} {category or ''}{comment_part}"


async def handle_intent_message(message: Message, state: FSMContext, text: str, source: str = "text") -> bool:
    from app.database import (
        get_last_transaction,
        get_transaction_by_id,
        delete_transaction_by_id,
    )
    from app.services.category_commands import apply_category_command
    from app.services.intent_router import parse_user_intent

    intent = await parse_user_intent(message.from_user.id, text, source)
    name = intent.get("intent")
    params = intent.get("params", {})

    if name == "unknown":
        return False

    if name == "open_main_menu":
        await state.clear()
        await message.answer("Выбирай действие:", reply_markup=main_menu())
        return True

    if name == "show_report":
        report_text, kb = await render_month_report(
            message.from_user.id,
            int(params["year"]),
            int(params["month"]),
        )
        await message.answer(report_text, parse_mode="HTML", reply_markup=kb)
        return True

    if name == "show_recent":
        txs = await get_recent_transactions(message.from_user.id, limit=10)
        if not txs:
            await message.answer(
                "Пока нет транзакций.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return True
        lines = []
        for tx in txs:
            sign = "-" if tx["type"] == "expense" else "+"
            date_value = tx["transaction_date"].strftime("%d.%m")
            comment = f" | {tx['comment']}" if tx.get("comment") else ""
            lines.append(
                f"#{tx['id']} {date_value} {sign}{abs(float(tx['amount'])):,.0f} "
                f"{tx.get('category_name') or ''}{comment}"
            )
        await message.answer(
            "Последние транзакции:\n\n" + "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть список", callback_data="recent")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return True

    if name == "show_calendar":
        await message.answer(
            "Открываю платёжный календарь.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗓 Календарь", callback_data="calendar")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return True

    if name == "delete_transaction":
        tx = None
        variants = []
        tx_id = params.get("tx_id")
        if tx_id:
            tx = await get_transaction_by_id(message.from_user.id, tx_id)
        elif params.get("last"):
            tx = await get_last_transaction(message.from_user.id)
        else:
            from app.services.transaction_commands import find_transaction_from_text
            tx, variants = await find_transaction_from_text(message.from_user.id, params.get("text") or text)

        if not tx:
            if variants:
                await message.answer(
                    "Нашёл несколько похожих транзакций:\n"
                    + "\n".join(_format_tx_preview(item) for item in variants)
                    + "\n\nНапиши номер нужной транзакции.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Удалить по номеру", callback_data="delete_by_id")],
                        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                    ]),
                )
                return True
            await message.answer(
                "Не нашёл транзакцию для удаления. Напиши номер, например: «удали транзакцию 42».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return True

        await message.answer(
            "Вы имеете в виду эту транзакцию?\n" + _format_tx_preview(tx),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_delete_intent:{tx[0]}"),
                    InlineKeyboardButton(text="Нет, введу номер", callback_data="cancel_delete_intent"),
                ],
            ]),
        )
        return True

    if name == "change_transaction_category":
        from app.services.transaction_commands import (
            find_transaction_for_category_change,
            parse_category_change,
        )
        parsed = await parse_category_change(message.from_user.id, params.get("text") or text)
        category_id = parsed.get("new_category_id")
        category_name = parsed.get("new_category_name")
        if not category_id:
            await message.answer(
                "Не понял новую категорию. Напиши, например: «поменяй категорию транзакции #42 на Транспорт».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return True
        tx, variants = await find_transaction_for_category_change(
            message.from_user.id,
            params.get("text") or text,
            parsed,
        )
        if not tx:
            if variants:
                await message.answer(
                    "Нашёл несколько похожих транзакций:\n"
                    + "\n".join(_format_tx_preview(item) for item in variants)
                    + "\n\nНапиши номер транзакции и новую категорию точнее.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                    ]),
                )
                return True
            await message.answer(
                "Не нашёл транзакцию для смены категории. Укажи номер или сумму/месяц точнее.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return True
        await message.answer(
            "Вы имеете в виду эту транзакцию?\n"
            + _format_tx_preview(tx)
            + f"\n\nПоменять категорию на «{category_name}»?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, поменять",
                        callback_data=f"confirm_change_tx_category:{tx[0]}:{category_id}",
                    ),
                    InlineKeyboardButton(text="Нет", callback_data="cancel_change_tx_category"),
                ],
            ]),
        )
        return True

    if name in ("rename_category", "add_category", "delete_category", "set_category_kind"):
        scope_type = params.get("scope_type", "expense")
        result = await apply_category_command(message.from_user.id, params.get("command"), scope_type)
        await message.answer(
            result,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Ручной ввод", callback_data="manual_input")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return True

    if name == "add_transaction":
        added = []
        saved_ids = []
        for tx in params.get("transactions", []):
            saved = await create_transaction(
                user_id=message.from_user.id,
                category_id=tx["category_id"],
                amount=tx["amount"],
                type_=tx["type"],
                kind=tx.get("kind"),
                comment=tx.get("comment") or "",
                transaction_date=tx.get("transaction_date"),
                pnl_period=tx.get("pnl_period"),
            )
            saved_ids.append(saved["id"])
            sign = "-" if tx["type"] == "expense" else "+"
            added.append(f"{sign}{int(float(tx['amount']))} руб. — {tx['category_name']} #{saved['id']}")

        if added:
            response_text = "Записано:\n" + "\n".join(added)
            insight = await build_first_transaction_insight(message.from_user.id, saved_ids)
            if insight:
                response_text += "\n\n" + insight
            await message.answer(
                response_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return True

    return False


async def send_text_to_ai_assistant(message: Message, state: FSMContext, user_id: int, user_text: str):
    from app.database import get_user_tier
    from app.handlers.ai_assistant import (
        AIState,
        check_ai_limit,
        get_ai_response,
        log_ai_usage,
        process_actions,
    )

    tier = await get_user_tier(user_id)
    if tier == "free":
        await message.answer(
            "ИИ-помощник доступен с тарифа Старт.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    can, used, limit = await check_ai_limit(user_id)
    if not can:
        await message.answer(
            "Лимит ИИ-помощника исчерпан на этот месяц.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    await state.set_state(AIState.chatting)
    thinking = await message.answer("Передаю в ИИ-помощник...")
    try:
        ai_text, new_history = await get_ai_response(user_id, user_text, [], tier=tier)
        await state.update_data(history=new_history[-20:])
        await log_ai_usage(user_id)
        clean_text, actions_log = await process_actions(user_id, ai_text)
        await thinking.delete()
        limit_str = "безлимит" if limit >= 9999 else str(used + 1) + "/" + str(limit)
        await message.answer(
            "🗣️ " + clean_text + actions_log + "\n\n[" + limit_str + "]",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
            ]),
        )
    except Exception as e:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer(
            "Не смог передать в ИИ-помощник: " + str(e),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )


@router.callback_query(F.data == "ask_ai_pending")
async def cb_ask_ai_pending(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    user_text = data.get("ai_pending_text")
    if not user_text:
        await call.message.answer(
            "Не нашёл текст для ИИ. Напиши вопрос ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="ИИ-помощник", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return
    await send_text_to_ai_assistant(call.message, state, call.from_user.id, user_text)


@router.message(F.text & ~F.text.startswith("/"), StateFilter(default_state))
async def msg_free_text(message: Message, state: FSMContext):
    # ИИ-хелпер — триггер по ключевым фразам
    text_lower = message.text.lower().strip()
    if _is_no_spend_reply(message.text):
        await message.answer(
            "Хорошо, тогда можно записать позже, когда появятся расходы.\n\n"
            "Например: «500 на продукты» или «кофе 250».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    if any(trigger in text_lower for trigger in HELP_TRIGGERS):
        thinking = await message.answer("Сейчас расскажу...")
        try:
            answer = await answer_help_question(message.text)
            await thinking.delete()
            await message.answer(
                "❓ " + answer,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
        except Exception as e:
            await thinking.delete()
            await message.answer("Ошибка хелпера: " + str(e))
        return

    handled = await handle_intent_message(message, state, message.text, source="text")
    if not handled:
        await state.update_data(ai_pending_text=message.text)
        await message.answer(
            "Не понял это как транзакцию или команду. Могу передать фразу в ИИ-помощник.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Спросить ИИ", callback_data="ask_ai_pending")],
                [InlineKeyboardButton(text="Открыть ИИ-помощник", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return


@router.message(Command("category"))
async def cmd_category(message: Message):
    from app.database import get_categories, execute
    args = message.text.strip()[len("/category"):].strip()

    if not args or args == "list":
        await message.answer(
            await render_categories_text(message.from_user.id),
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    # rename
    if args.startswith("rename"):
        import re
        m = re.findall(r'"([^"]+)"', args)
        if len(m) < 2:
            await message.answer('Формат: /category rename "Старое" "Новое"')
            return
        old_name, new_name = m[0], m[1]
        result = await execute(
            "UPDATE categories SET name=%s WHERE name=%s AND user_id=%s",
            (new_name, old_name, message.from_user.id)
        )
        await message.answer(f'Категория "{old_name}" → "{new_name}"' if result else f'Категория "{old_name}" не найдена.')
        return

    # add
    if args.startswith("add"):
        import re
        m = re.findall(r'"([^"]+)"', args)
        type_ = 'expense' if 'income' not in args else 'income'
        if not m:
            await message.answer('Формат: /category add "Название" expense')
            return
        name = m[0]
        kind = "income" if type_ == "income" else "variable"
        await execute(
            "INSERT INTO categories (user_id, name, type, kind) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (message.from_user.id, name, type_, kind)
        )
        type_ru = "доход" if type_ == 'income' else "расход"
        await message.answer(f'Категория "{name}" ({type_ru}) добавлена.')
        return

    # delete
    if args.startswith("delete"):
        import re
        m = re.findall(r'"([^"]+)"', args)
        if not m:
            await message.answer('Формат: /category delete "Название"')
            return
        name = m[0]
        await execute(
            "DELETE FROM categories WHERE name=%s AND user_id=%s",
            (name, message.from_user.id)
        )
        await message.answer(f'Категория "{name}" удалена.')
        return

    await message.answer("Неизвестная команда. Используй: list, rename, add, delete")
