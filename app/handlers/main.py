from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from app.database import (
    execute,
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
            callback_data=f"report:{y}:{m}"
        )] for y, m in months],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Отчёт ДДС — выбери месяц:", parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("report:"))
async def cb_report_by_month(call: CallbackQuery):
    _, year, month = call.data.split(":")
    year, month = int(year), int(month)
    summary = await get_monthly_summary(call.from_user.id, year, month)
    breakdown = await get_category_breakdown(call.from_user.id, year, month)
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    carry = summary.get("carry_over", 0.0)
    closing = summary.get("closing_balance", summary["balance"])
    total_exp = summary["total_expense"]
    pct_fixed = (summary["expense_fixed"] / total_exp * 100) if total_exp > 0 else 0
    pct_var = (summary["expense_variable"] / total_exp * 100) if total_exp > 0 else 0
    carry_str = f"+{carry:,.0f}" if carry >= 0 else f"{carry:,.0f}"
    closing_icon = "✅" if closing >= 0 else "🔴"

    text = (
        f"Отчёт ДДС — {MONTHS[month]} {year}

"
        f"Входящий остаток: {carry_str} руб.

"
        f"Доходы: {summary['income']:,.0f} руб.
"
        f"Расходы: -{summary['total_expense']:,.0f} руб.
"
        f"  Постоянные: {summary['expense_fixed']:,.0f} ({pct_fixed:.0f}%)
"
        f"  Переменные: {summary['expense_variable']:,.0f} ({pct_var:.0f}%)

"
        f"{closing_icon} Остаток на конец: {closing:,.0f} руб.
"
    )
    if breakdown:
        text += "
Топ расходов:
"
        for row in breakdown[:6]:
            icon = "📌" if row["kind"] == "fixed" else "🛒"
            text += f"  {icon} {row['name']}: {float(row['total']):,.0f}
"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="report_month")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, parse_mode=None, reply_markup=kb)


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
        [InlineKeyboardButton(text="Выгрузить всё в Excel", callback_data="export_excel")],
        [InlineKeyboardButton(text="Удалить транзакцию", callback_data="delete_by_id")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Транзакции — выбери месяц:", parse_mode=None, reply_markup=kb)


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
    await execute(
        "DELETE FROM transactions WHERE id=%s AND user_id=%s",
        (tx_id, call.from_user.id)
    )
    await call.message.edit_text("Транзакция удалена.", reply_markup=main_menu())


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

@router.message(F.text.regexp(r'^[+-]?\d+'), StateFilter(default_state))
async def msg_quick_input(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        return

    from app.parser import parse_quick_input
    from app.database import fetchone
    parsed = parse_quick_input(message.text)

    if not parsed:
        return

    categories = await get_categories(message.from_user.id, type_=parsed['type'])
    category_id = None
    kind = 'variable' if parsed['type'] == 'expense' else 'income'

    if parsed['category_hint']:
        for cat in categories:
            if parsed['category_hint'].lower() in cat['name'].lower():
                category_id = cat['id']
                kind = cat['kind']
                break

    # Если знак не указан явно и нашли расходную категорию — меняем тип на расход
    if not parsed.get('sign_explicit', True) and parsed['category_hint']:
        for cat in categories:
            if parsed['category_hint'].lower() in cat['name'].lower():
                if cat['type'] == 'expense':
                    parsed['type'] = 'expense'
                    categories = await get_categories(message.from_user.id, type_='expense')
                break

    if not category_id and categories:
        # Ищем "Прочие расходы" или "Прочие доходы" как дефолт
        default_name = 'Прочие расходы' if parsed["type"] == 'expense' else 'Прочие доходы'
        for cat in categories:
            if default_name.lower() in cat['name'].lower():
                category_id = cat['id']
                kind = cat['kind']
                break
        if not category_id:
            category_id = categories[0]['id']
            kind = categories[0]['kind']

    if not category_id:
        await message.answer("❌ Не нашёл подходящую категорию.")
        return

    row = await fetchone(
        """INSERT INTO transactions
           (user_id, category_id, amount, type, kind, comment,
            transaction_date, wallet, pnl_period)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (message.from_user.id, category_id, parsed['amount'],
         parsed['type'], kind, parsed['comment'],
         parsed['transaction_date'], parsed['wallet'], parsed['pnl_period'])
    )

    tx_id = row[0]
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
                InlineKeyboardButton(text="✅ Верно", callback_data=f"confirm:{tx_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tx:{tx_id}"),
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
    _, year, month = call.data.split(":")
    year, month = int(year), int(month)
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

    text = f"Транзакции за {MONTHS[month]} {year}\n\n"
    for tx in txs:
        tx_id, date, amount, type_, comment, cat_name, wallet = tx
        sign = "-" if type_ == "expense" else "+"
        wallet_str = {"cash": "нал", "card": "бн"}.get(wallet, "")
        comment_str = f" | {comment}" if comment else ""
        text += f"#{tx_id} {date.strftime('%d.%m')} {sign}{float(amount):,.0f} {cat_name or ''} {wallet_str}{comment_str}\n"

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить транзакцию", callback_data="delete_by_id")],
            [InlineKeyboardButton(text="Назад", callback_data="recent")],
        ])
    )


# --- Удаление по номеру ---

class DeleteTxState(StatesGroup):
    waiting_id = State()


@router.callback_query(F.data == "delete_by_id")
async def cb_delete_by_id(call: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteTxState.waiting_id)
    await call.message.edit_text(
        "Напишите номер транзакции для удаления (например: 42):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="recent")]
        ])
    )


@router.message(DeleteTxState.waiting_id)
async def msg_delete_tx_by_id(message: Message, state: FSMContext):
    from app.database import delete_transaction_by_id
    await state.clear()
    try:
        tx_id = int(message.text.strip().replace("#", ""))
    except ValueError:
        await message.answer("Введи числовой номер транзакции, например: 42")
        return

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
    ws.append(["#", "Дата", "Сумма", "Тип", "Категория", "Кошелёк", "Комментарий", "ПнЛ период"])

    for tx in txs:
        tx_id, date, amount, type_, cat, wallet, comment, pnl = tx
        ws.append([
            tx_id,
            date.strftime("%d.%m.%Y"),
            float(amount) if type_ == "income" else -float(amount),
            "Доход" if type_ == "income" else "Расход",
            cat or "",
            wallet or "",
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
    now = datetime.now()
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}
    # Прошлый месяц
    pm = now.month - 1
    py = now.year
    if pm <= 0:
        pm += 12
        py -= 1
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Текущий — {MONTHS[now.month]} {now.year}", callback_data=f"pnl:{now.year}:{now.month}")],
        [InlineKeyboardButton(text=f"Прошлый — {MONTHS[pm]} {py}", callback_data=f"pnl:{py}:{pm}")],
        [InlineKeyboardButton(text="Выгрузить за год в Excel", callback_data="pnl_export_year")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("ПнЛ отчёт — выбери период:", parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("pnl:"))
async def cb_pnl_by_month(call: CallbackQuery):
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

    _, year, month = call.data.split(":")
    year, month = int(year), int(month)
    d = await get_pnl_report(call.from_user.id, year, month)

    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    pct = d['pct']

    text = f"ПнЛ — {MONTHS[month]} {year}\n\n"

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


# --- Экспорт ПнЛ за год ---

class PnlExportState(StatesGroup):
    waiting_year = State()


class DashboardExportState(StatesGroup):
    waiting_year = State()


@router.callback_query(F.data == "pnl_export_year")
async def cb_pnl_export_year(call: CallbackQuery, state: FSMContext):
    await state.set_state(PnlExportState.waiting_year)
    now = datetime.now()
    await call.message.edit_text(
        f"За какой год выгрузить ПнЛ?\nВведи год (например: {now.year}):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="pnl_report")]
        ])
    )


@router.message(PnlExportState.waiting_year)
async def msg_pnl_export_year(message: Message, state: FSMContext):
    from app.database import get_pnl_report
    import io, openpyxl
    await state.clear()
    try:
        year = int(message.text.strip())
        if year < 2020 or year > 2030:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный год, например: 2026")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"ПнЛ {year}"
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    # Заголовки
    headers = ["Показатель"] + [MONTHS[m] for m in range(1, 13)]
    ws.append(headers)

    # Собираем данные по всем месяцам
    all_data = {}
    for m in range(1, 13):
        all_data[m] = await get_pnl_report(message.from_user.id, year, m)

    rows = [
        ("Выручка", lambda d: d['income']),
        ("Переменные расходы", lambda d: -d['variable']),
        ("Маржинальная прибыль", lambda d: d['gross_profit']),
        ("Постоянные расходы", lambda d: -d['fixed']),
        ("EBITDA", lambda d: d['ebitda']),
        ("Амортизация", lambda d: -d['depreciation']),
        ("Налоги", lambda d: -d['tax']),
        ("Кредит (тело)", lambda d: -d['loan_body']),
        ("Кредит (проценты)", lambda d: -d['loan_pct']),
        ("Чистая прибыль", lambda d: d['net_profit']),
    ]

    for label, fn in rows:
        row = [label] + [fn(all_data[m]) for m in range(1, 13)]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from aiogram.types import BufferedInputFile
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=f"pnl_{year}.xlsx"),
        caption=f"ПнЛ за {year} год"
    )


# --- Дашборд с выбором месяца ---

@router.callback_query(F.data == "dashboard")
async def cb_dashboard_menu(call: CallbackQuery):
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
            callback_data=f"dash:{y}:{m}"
        )] for y, m in months],
        [InlineKeyboardButton(text="Выгрузить за год в Excel", callback_data="dashboard_export_year")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Табло управленца — выбери месяц:", parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("dash:"))
async def cb_dashboard_by_month(call: CallbackQuery):
    from app.database import get_dashboard, can_use_feature
    if not await can_use_feature(call.from_user.id, 'business_tools'):
        await call.message.edit_text(
            "Табло управленца доступно на тарифе Business.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    _, year, month = call.data.split(":")
    year, month = int(year), int(month)
    d = await get_dashboard(call.from_user.id, year, month)

    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    if d['dynamics'] is not None:
        dyn_icon = "📈" if d['dynamics'] >= 0 else "📉"
        dyn_str = f"{dyn_icon} {d['dynamics']:+.1f}% vs прошлый месяц"
    else:
        dyn_str = "Нет данных за прошлый месяц"

    upcoming_str = ""
    if d['upcoming']:
        upcoming_str = "\nБлижайшие платежи (7 дней):\n"
        for p in d['upcoming']:
            upcoming_str += f"  {p[2].strftime('%d.%m')} {p[0]} — {float(p[1]):,.0f} руб.\n"

    top_cats = sorted([c for c in d['categories'] if c[1] in ('fixed','variable')],
                      key=lambda x: x[2], reverse=True)[:3]
    top_str = ""
    if top_cats:
        top_str = "\nТоп расходов:\n"
        for name, kind, total in top_cats:
            pct = total / d['income'] * 100 if d['income'] > 0 else 0
            top_str += f"  {name}: {total:,.0f} ({pct:.1f}%)\n"

    text = (
        f"Табло управленца — {MONTHS[month]} {year}\n\n"
        f"Выручка: {d['income']:,.0f} руб.\n"
        f"Переменные расходы: -{d['variable_expense']:,.0f} руб.\n"
        f"Постоянные расходы: -{d['fixed_expense']:,.0f} руб.\n\n"
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
            [InlineKeyboardButton(text="Назад", callback_data="dashboard")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data == "dashboard_export_year")
async def cb_dashboard_export_year(call: CallbackQuery, state: FSMContext):
    await state.set_state(DashboardExportState.waiting_year)
    now = datetime.now()
    await call.message.edit_text(
        f"За какой год выгрузить табло?\nВведи год (например: {now.year}):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="dashboard")]
        ])
    )


@router.message(DashboardExportState.waiting_year)
async def msg_dashboard_export_year(message: Message, state: FSMContext):
    from app.database import get_dashboard
    import io, openpyxl
    await state.clear()
    try:
        year = int(message.text.strip())
        if year < 2020 or year > 2030:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный год, например: 2026")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Дашборд {year}"
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    headers = ["Показатель"] + [MONTHS[m] for m in range(1, 13)]
    ws.append(headers)

    all_data = {}
    for m in range(1, 13):
        all_data[m] = await get_dashboard(message.from_user.id, year, m)

    rows = [
        ("Выручка", lambda d: d['income']),
        ("Переменные расходы", lambda d: -d['variable_expense']),
        ("Постоянные расходы", lambda d: -d['fixed_expense']),
        ("EBITDA", lambda d: d['ebitda']),
        ("Чистая прибыль", lambda d: d['net_profit']),
        ("% ЧП", lambda d: round(d['net_profit_pct'], 1)),
        ("Транзакций", lambda d: d['tx_count']),
    ]

    for label, fn in rows:
        row = [label] + [fn(all_data[m]) for m in range(1, 13)]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from aiogram.types import BufferedInputFile
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=f"dashboard_{year}.xlsx"),
        caption=f"Табло управленца за {year} год"
    )


# --- Экспорт ПнЛ за год ---

class PnlExportState(StatesGroup):
    waiting_year = State()


class DashboardExportState(StatesGroup):
    waiting_year = State()


@router.callback_query(F.data == "pnl_export_year")
async def cb_pnl_export_year(call: CallbackQuery, state: FSMContext):
    await state.set_state(PnlExportState.waiting_year)
    now = datetime.now()
    await call.message.edit_text(
        f"За какой год выгрузить ПнЛ?\nВведи год (например: {now.year}):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="pnl_report")]
        ])
    )


@router.message(PnlExportState.waiting_year)
async def msg_pnl_export_year(message: Message, state: FSMContext):
    from app.database import get_pnl_report
    import io, openpyxl
    await state.clear()
    try:
        year = int(message.text.strip())
        if year < 2020 or year > 2030:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный год, например: 2026")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"ПнЛ {year}"
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    # Заголовки
    headers = ["Показатель"] + [MONTHS[m] for m in range(1, 13)]
    ws.append(headers)

    # Собираем данные по всем месяцам
    all_data = {}
    for m in range(1, 13):
        all_data[m] = await get_pnl_report(message.from_user.id, year, m)

    rows = [
        ("Выручка", lambda d: d['income']),
        ("Переменные расходы", lambda d: -d['variable']),
        ("Маржинальная прибыль", lambda d: d['gross_profit']),
        ("Постоянные расходы", lambda d: -d['fixed']),
        ("EBITDA", lambda d: d['ebitda']),
        ("Амортизация", lambda d: -d['depreciation']),
        ("Налоги", lambda d: -d['tax']),
        ("Кредит (тело)", lambda d: -d['loan_body']),
        ("Кредит (проценты)", lambda d: -d['loan_pct']),
        ("Чистая прибыль", lambda d: d['net_profit']),
    ]

    for label, fn in rows:
        row = [label] + [fn(all_data[m]) for m in range(1, 13)]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from aiogram.types import BufferedInputFile
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=f"pnl_{year}.xlsx"),
        caption=f"ПнЛ за {year} год"
    )


# --- Дашборд с выбором месяца ---

@router.callback_query(F.data == "dashboard")
async def cb_dashboard_menu(call: CallbackQuery):
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
            callback_data=f"dash:{y}:{m}"
        )] for y, m in months],
        [InlineKeyboardButton(text="Выгрузить за год в Excel", callback_data="dashboard_export_year")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text("Табло управленца — выбери месяц:", parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("dash:"))
async def cb_dashboard_by_month(call: CallbackQuery):
    from app.database import get_dashboard, can_use_feature
    if not await can_use_feature(call.from_user.id, 'business_tools'):
        await call.message.edit_text(
            "Табло управленца доступно на тарифе Business.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    _, year, month = call.data.split(":")
    year, month = int(year), int(month)
    d = await get_dashboard(call.from_user.id, year, month)

    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    if d['dynamics'] is not None:
        dyn_icon = "📈" if d['dynamics'] >= 0 else "📉"
        dyn_str = f"{dyn_icon} {d['dynamics']:+.1f}% vs прошлый месяц"
    else:
        dyn_str = "Нет данных за прошлый месяц"

    upcoming_str = ""
    if d['upcoming']:
        upcoming_str = "\nБлижайшие платежи (7 дней):\n"
        for p in d['upcoming']:
            upcoming_str += f"  {p[2].strftime('%d.%m')} {p[0]} — {float(p[1]):,.0f} руб.\n"

    top_cats = sorted([c for c in d['categories'] if c[1] in ('fixed','variable')],
                      key=lambda x: x[2], reverse=True)[:3]
    top_str = ""
    if top_cats:
        top_str = "\nТоп расходов:\n"
        for name, kind, total in top_cats:
            pct = total / d['income'] * 100 if d['income'] > 0 else 0
            top_str += f"  {name}: {total:,.0f} ({pct:.1f}%)\n"

    text = (
        f"Табло управленца — {MONTHS[month]} {year}\n\n"
        f"Выручка: {d['income']:,.0f} руб.\n"
        f"Переменные расходы: -{d['variable_expense']:,.0f} руб.\n"
        f"Постоянные расходы: -{d['fixed_expense']:,.0f} руб.\n\n"
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
            [InlineKeyboardButton(text="Назад", callback_data="dashboard")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data == "dashboard_export_year")
async def cb_dashboard_export_year(call: CallbackQuery, state: FSMContext):
    await state.set_state(DashboardExportState.waiting_year)
    now = datetime.now()
    await call.message.edit_text(
        f"За какой год выгрузить табло?\nВведи год (например: {now.year}):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="dashboard")]
        ])
    )


@router.message(DashboardExportState.waiting_year)
async def msg_dashboard_export_year(message: Message, state: FSMContext):
    from app.database import get_dashboard
    import io, openpyxl
    await state.clear()
    try:
        year = int(message.text.strip())
        if year < 2020 or year > 2030:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный год, например: 2026")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Дашборд {year}"
    MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
              7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    headers = ["Показатель"] + [MONTHS[m] for m in range(1, 13)]
    ws.append(headers)

    all_data = {}
    for m in range(1, 13):
        all_data[m] = await get_dashboard(message.from_user.id, year, m)

    rows = [
        ("Выручка", lambda d: d['income']),
        ("Переменные расходы", lambda d: -d['variable_expense']),
        ("Постоянные расходы", lambda d: -d['fixed_expense']),
        ("EBITDA", lambda d: d['ebitda']),
        ("Чистая прибыль", lambda d: d['net_profit']),
        ("% ЧП", lambda d: round(d['net_profit_pct'], 1)),
        ("Транзакций", lambda d: d['tx_count']),
    ]

    for label, fn in rows:
        row = [label] + [fn(all_data[m]) for m in range(1, 13)]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from aiogram.types import BufferedInputFile
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=f"dashboard_{year}.xlsx"),
        caption=f"Табло управленца за {year} год"
    )
