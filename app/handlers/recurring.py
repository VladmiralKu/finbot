from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import date

from app.database import get_recurring_payments, add_recurring_payment, get_categories, is_premium, can_use_feature

router = Router()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class AddRecurring(StatesGroup):
    name = State()
    amount = State()
    repeat_type = State()
    repeat_day = State()
    remind_before = State()


@router.callback_query(F.data == "calendar")
async def cb_calendar(call: CallbackQuery):
    if not await can_use_feature(call.from_user.id, "calendar"):
        await call.message.edit_text(
            "🗓 Платёжный календарь\n\nЭта функция доступна с тарифа Старт и выше.\n\nИспользуй /premium чтобы узнать о тарифах.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
            ])
        )
        return

    payments = await get_recurring_payments(call.from_user.id)
    today = date.today()

    if not payments:
        text = "🗓 Платёжный календарь\n\nУ тебя пока нет регулярных платежей.\n\nДобавь первый — и я буду напоминать тебе о нём!"
    else:
        text = "🗓 Платёжный календарь\n\n"
        for p in payments:
            next_date = p[14]
            days_left = (next_date - today).days
            icon = "🔴" if days_left <= 1 else "🟡" if days_left <= 3 else "🟢"
            amount_str = f"~{abs(float(p[3])):,.0f}" if p[4] else f"{abs(float(p[3])):,.0f}"
            repeat = f"каждый {p[9]}й" if p[8] == 'monthly' else f"каждый {WEEKDAYS[p[10]]}"
            text += f"{icon} {p[2]} — {amount_str} руб.\n"
            text += f"   {next_date.strftime('%d.%m')} ({repeat}) — через {days_left} д.\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить платёж", callback_data="add_recurring")],
        [InlineKeyboardButton(text="🗑 Удалить платёж", callback_data="delete_recurring_menu")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data == "delete_recurring_menu")
async def cb_delete_recurring_menu(call: CallbackQuery):
    payments = await get_recurring_payments(call.from_user.id)
    if not payments:
        await call.answer("Нет платежей для удаления.")
        return

    buttons = []
    for p in payments:
        label = f"🗑 {p[2]} — {float(p[3]):,.0f} руб."
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"delete_recurring:{p[0]}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="calendar")])

    await call.message.edit_text(
        "Выбери платёж для удаления:",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("delete_recurring:"))
async def cb_delete_recurring(call: CallbackQuery):
    from app.database import execute
    payment_id = int(call.data.split(":")[1])
    await execute(
        "DELETE FROM recurring_payments WHERE id = %s AND user_id = %s",
        (payment_id, call.from_user.id)
    )
    await call.answer("Платёж удалён.")
    await cb_calendar(call)


@router.callback_query(F.data == "add_recurring")
async def cb_add_recurring(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddRecurring.name)
    await call.message.edit_text(
        "Новый регулярный платёж\n\nКак называется? (например: Аренда, Кредит, Продукты)",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="calendar")]
        ])
    )


@router.message(AddRecurring.name)
async def msg_recurring_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddRecurring.amount)
    await message.answer("Введи сумму (примерную тоже можно, например: 2500):")


@router.message(AddRecurring.amount)
async def msg_recurring_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", ".").replace(" ", "").replace("~", ""))
        is_approx = "~" in message.text
    except ValueError:
        await message.answer("Введи число, например: 2500")
        return
    await state.update_data(amount=amount, is_approx=is_approx)
    await state.set_state(AddRecurring.repeat_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ежемесячно", callback_data="rtype:monthly")],
        [InlineKeyboardButton(text="Еженедельно", callback_data="rtype:weekly")],
    ])
    await message.answer("Как часто повторяется?", reply_markup=kb)


@router.callback_query(F.data.startswith("rtype:"))
async def cb_repeat_type(call: CallbackQuery, state: FSMContext):
    rtype = call.data.split(":")[1]
    await state.update_data(repeat_type=rtype)
    await state.set_state(AddRecurring.repeat_day)
    if rtype == "monthly":
        await call.message.edit_text("В какой день месяца? (введи число от 1 до 28):")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=day, callback_data=f"rday:{i}") for i, day in enumerate(WEEKDAYS[:4])],
            [InlineKeyboardButton(text=day, callback_data=f"rday:{i+4}") for i, day in enumerate(WEEKDAYS[4:])],
        ])
        await call.message.edit_text("В какой день недели?", reply_markup=kb)


@router.message(AddRecurring.repeat_day)
async def msg_repeat_day(message: Message, state: FSMContext):
    try:
        day = int(message.text)
        if not 1 <= day <= 28:
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 1 до 28")
        return
    await state.update_data(repeat_day_month=day)
    await _ask_remind(message, state)


@router.callback_query(F.data.startswith("rday:"))
async def cb_repeat_day_week(call: CallbackQuery, state: FSMContext):
    day = int(call.data.split(":")[1])
    await state.update_data(repeat_day_week=day)
    await _ask_remind(call.message, state)


async def _ask_remind(message, state):
    await state.set_state(AddRecurring.remind_before)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="В день платежа", callback_data="remind:0"),
            InlineKeyboardButton(text="За 1 день", callback_data="remind:1"),
        ],
        [InlineKeyboardButton(text="За 3 дня", callback_data="remind:3")],
    ])
    await message.answer("Когда напомнить?", reply_markup=kb)


@router.callback_query(F.data.startswith("remind:"))
async def cb_remind(call: CallbackQuery, state: FSMContext):
    remind = int(call.data.split(":")[1])
    data = await state.get_data()
    await state.clear()

    categories = await get_categories(call.from_user.id, type_="expense")
    cat_id = categories[0]["id"] if categories else None

    await add_recurring_payment(
        user_id=call.from_user.id,
        name=data["name"],
        amount=data["amount"],
        type_="expense",
        kind="fixed",
        category_id=cat_id,
        repeat_type=data["repeat_type"],
        repeat_day_of_month=data.get("repeat_day_month"),
        repeat_day_of_week=data.get("repeat_day_week"),
        remind_days_before=remind,
        amount_is_approximate=data.get("is_approx", False),
    )

    remind_text = "в день платежа" if remind == 0 else f"за {remind} д."
    await call.message.edit_text(
        f"{data['name']} добавлен в регулярные платежи!\n\nБуду напоминать {remind_text}.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Мой календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data.startswith("paid:"))
async def cb_paid_recurring(call: CallbackQuery):
    from app.database import fetchone, get_categories, add_transaction
    parts = call.data.split(":")
    payment_id = int(parts[1])

    # Получаем инфо о платеже из БД (включая сумму — не доверяем callback_data)
    payment = await fetchone(
        "SELECT name, user_id, kind, amount FROM recurring_payments WHERE id = %s AND user_id = %s",
        (payment_id, call.from_user.id)
    )
    if not payment:
        await call.answer("Платёж не найден.")
        return

    # fetchone может вернуть dict или tuple
    if isinstance(payment, dict):
        pay_name = payment['name']
        pay_kind = payment.get('kind') or 'fixed'
        amount = float(payment['amount'])
    else:
        pay_name = payment[0]
        pay_kind = payment[2] or 'fixed'
        amount = float(payment[3])

    # Находим категорию
    categories = await get_categories(call.from_user.id)
    category_id = None
    for cat in categories:
        if 'прочие' in cat['name'].lower() and cat.get('type') == 'expense':
            category_id = cat['id']
            break
    if not category_id and categories:
        for cat in categories:
            if cat.get('type') == 'expense':
                category_id = cat['id']
                break

    if category_id:
        await add_transaction(
            call.from_user.id,
            category_id=category_id,
            amount=amount,
            type_='expense',
            kind=pay_kind,
            comment=pay_name
        )

    await call.message.edit_text(
        f"✅ <b>Оплачено!</b>\n\n"
        f"📌 {pay_name} — {amount:,.0f} ₽\n"
        f"Транзакция записана.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data.startswith("postpone:"))
async def cb_postpone_recurring(call: CallbackQuery):
    from app.database import execute
    from datetime import date, timedelta
    payment_id = int(call.data.split(":")[1])

    # Переносим на завтра — сбрасываем last_triggered_at (только если платёж принадлежит этому пользователю)
    await execute(
        "UPDATE recurring_payments SET last_triggered_at = NULL WHERE id = %s AND user_id = %s",
        (payment_id, call.from_user.id)
    )

    await call.message.edit_text(
        "❌ Напомню завтра.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )
