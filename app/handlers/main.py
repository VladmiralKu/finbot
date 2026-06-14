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
    try:
        await call.message.edit_text("Выбирай действие:", reply_markup=main_menu())
    except Exception:
        await call.message.answer("Выбирай действие:", reply_markup=main_menu())


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


@router.callback_query(F.data.startswith("report:"))
async def cb_report_by_month(call: CallbackQuery):
    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    now = type("obj", (object,), {"year": year, "month": month})()
    summary = await get_monthly_summary(call.from_user.id, year, month)
    breakdown = await get_category_breakdown(call.from_user.id, year, month)
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
    # Все расходные категории с % от доходов
    income = summary['income'] if summary['income'] else 1
    balance = summary['balance']
    balance_pct = (balance / income * 100) if income else 0

    # Получаем все категории пользователя
    all_cats = await get_categories(call.from_user.id)
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
            pct = (total / income * 100) if income else 0
            icon = "🔒" if expense_kinds.get(name) == 'fixed' else "🛒"
            text += f"  {icon} {name}: {total:,.0f} ₽ ({pct:.0f}%)\n"

    # Добавляем % к остатку
    text = text.replace(
        f"<b>{balance:+,.0f} ₽</b>",
        f"<b>{balance:+,.0f} ₽ ({balance_pct:.0f}%)</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 График", callback_data="chart:" + str(year) + ":" + str(month))],
        [InlineKeyboardButton(text="🤖 ИИ-ассистент", callback_data="ai_assistant")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
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

@router.message(F.text.regexp(r'^[+-]?\d+(?![.\d])'), StateFilter(default_state))
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
            [InlineKeyboardButton(text="✏️ Изменить транзакцию", callback_data=f"edit_by_id:{year}:{month}")],
            [InlineKeyboardButton(text="🗑 Удалить транзакцию", callback_data="delete_by_id")],
            [InlineKeyboardButton(text="Назад", callback_data="recent")],
        ])
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
        if hint and hint.lower() in cat['name'].lower():
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
        ws3.append([r["id"], r["text"], str(r["created_at"])])

    # 4. Цели
    ws4 = wb.create_sheet("Цели")
    ws4.append(["ID", "Текст", "Создано"])
    goals = await fetchall("SELECT id, goal_text, created_at FROM user_goals WHERE user_id = %s ORDER BY created_at DESC", (call.from_user.id,))
    for r in goals:
        ws4.append([r["id"], r["goal_text"], str(r["created_at"])])

    # 5. История ИИ
    try:
        ws5 = wb.create_sheet("Диалоги ИИ")
        ws5.append(["ID", "Роль", "Сообщение", "Дата"])
        history = await fetchall("SELECT id, role, content, created_at FROM ai_history WHERE user_id = %s ORDER BY created_at", (call.from_user.id,))
        for r in history:
            ws5.append([r["id"], r["role"], r["content"], str(r["created_at"])])
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


@router.message(F.text, StateFilter(default_state))
async def msg_free_text(message: Message):
    from app.database import get_user_tier, get_categories, add_transaction
    from app.parser import parse_quick_input
    from app.handlers.voice import parse_voice_to_transaction

    # Игнорируем команды
    if message.text.startswith('/'):
        return

    tier = await get_user_tier(message.from_user.id)

    # Пробуем распарсить как быстрый ввод
    parsed = parse_quick_input(message.text)
    if parsed and parsed.get('amount'):
        categories = await get_categories(message.from_user.id)
        amount = parsed.get('amount')
        type_ = parsed.get('type', 'expense')
        hint = parsed.get('category_hint', '')

        category_id = None
        category_name = ''
        for cat in categories:
            if hint and hint.lower() in cat['name'].lower():
                category_id = cat['id']
                category_name = cat['name']
                break
        if not category_id:
            for cat in categories:
                if cat.get('type') == type_:
                    category_id = cat['id']
                    category_name = cat['name']
                    break

        if category_id:
            await add_transaction(
                message.from_user.id,
                category_id=category_id,
                amount=amount,
                type_=type_,
                kind=parsed.get('kind', 'variable'),
                comment=parsed.get('comment', '')
            )
            sign = "-" if type_ == 'expense' else "+"
            await message.answer(
                "Записано: " + sign + str(int(amount)) + " руб. — " + category_name,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
            return

    # Если не распарсилось — пробуем через GPT
    if tier == 'free':
        return

    thinking = await message.answer("Думаю...")
    try:
        tx_lines = await parse_voice_to_transaction(message.text)
        categories = await get_categories(message.from_user.id)
        added = []

        for tx_str in tx_lines:
            parsed = parse_quick_input(tx_str)
            if not parsed or not parsed.get('amount'):
                continue
            amount = parsed.get('amount')
            type_ = parsed.get('type', 'expense')
            hint = parsed.get('category_hint', '')

            category_id = None
            category_name = ''
            for cat in categories:
                if hint and hint.lower() in cat['name'].lower():
                    category_id = cat['id']
                    category_name = cat['name']
                    break
            if not category_id:
                for cat in categories:
                    if 'прочие' in cat['name'].lower() and cat.get('type') == type_:
                        category_id = cat['id']
                        category_name = cat['name']
                        break
            if not category_id:
                for cat in categories:
                    if cat.get('type') == type_:
                        category_id = cat['id']
                        category_name = cat['name']
                        break

            if category_id:
                await add_transaction(
                    message.from_user.id,
                    category_id=category_id,
                    amount=amount,
                    type_=type_,
                    kind=parsed.get('kind', 'variable'),
                    comment=parsed.get('comment', '')
                )
                sign = "-" if type_ == 'expense' else "+"
                added.append(sign + str(int(amount)) + " руб. — " + category_name)

        await thinking.delete()
        if added:
            await message.answer(
                "Записано: " + ", ".join(added),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
    except Exception as e:
        await thinking.delete()
        await message.answer("Ошибка: " + str(e))


@router.message(Command("category"))
async def cmd_category(message: Message):
    from app.database import get_categories, execute
    args = message.text.strip()[len("/category"):].strip()

    if not args or args == "list":
        cats = await get_categories(message.from_user.id)
        if not cats:
            await message.answer("Категорий нет.")
            return
        income = [c for c in cats if c.get('type') == 'income']
        expense = [c for c in cats if c.get('type') == 'expense']
        text = "📂 Твои категории:\n\n"
        text += "💰 Доходы:\n"
        for c in income:
            text += f"  • {c['name']}\n"
        text += "\n💸 Расходы:\n"
        for c in expense:
            text += f"  • {c['name']}\n"
        text += "\nКоманды:\n/category rename \"Старое\" \"Новое\"\n/category add \"Название\" expense\n/category add \"Название\" income\n/category delete \"Название\""
        await message.answer(text, parse_mode=None)
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
        await execute(
            "INSERT INTO categories (user_id, name, type_, kind) VALUES (%s, %s, %s, 'variable') ON CONFLICT DO NOTHING",
            (message.from_user.id, name, type_)
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
