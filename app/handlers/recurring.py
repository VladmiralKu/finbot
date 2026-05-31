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
            "🗓 <b>Платёжный календарь</b>

"
            "Эта функция доступна с тарифа <b>Старт</b> и выше.

"
            "Используй /premium чтобы узнать о тарифах.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
            ])
        )
        return
    payments = await get_recurring_payments(call.from_user.id)
    today = date.today()

    if not payments:
        text = (
            "🗓 <b>Платёжный календарь</b>\n\n"
            "У тебя пока нет регулярных платежей.\n\n"
            "Добавь первый — и я буду напоминать тебе о нём!"
        )
    else:
        text = "🗓 <b>Платёжный календарь</b>\n\n"
        for p in payments:
            next_date = p[14]  # next_trigger_date
            days_left = (next_date - today).days
            icon = "🔴" if days_left <= 1 else "🟡" if days_left <= 3 else "🟢"
            amount_str = f"~{float(p[3]):,.0f}" if p[4] else f"{float(p[3]):,.0f}"
            repeat = f"каждый {p[9]}й" if p[8] == 'monthly' else f"каждый {WEEKDAYS[p[10]]}"
            text += f"{icon} <b>{p[2]}</b> — {amount_str} ₽\n"
            text += f"   📅 {next_date.strftime('%d.%m')} ({repeat})"
            text += f" • через {days_left} д.\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить платёж", callback_data="add_recurring")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "add_recurring")
async def cb_add_recurring(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddRecurring.name)
    await call.message.edit_text(
        "➕ <b>Новый регулярный платёж</b>\n\nКак называется? (например: Аренда, Кредит, Продукты)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="calendar")]
        ])
    )


@router.message(AddRecurring.name)
async def msg_recurring_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddRecurring.amount)
    await message.answer("💰 Введи сумму (примерную тоже можно, например: 2500):")


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
        [InlineKeyboardButton(text="📅 Ежемесячно", callback_data="rtype:monthly")],
        [InlineKeyboardButton(text="📆 Еженедельно", callback_data="rtype:weekly")],
    ])
    await message.answer("🔄 Как часто повторяется?", reply_markup=kb)


@router.callback_query(F.data.startswith("rtype:"))
async def cb_repeat_type(call: CallbackQuery, state: FSMContext):
    rtype = call.data.split(":")[1]
    await state.update_data(repeat_type=rtype)
    await state.set_state(AddRecurring.repeat_day)
    if rtype == "monthly":
        await call.message.edit_text("📅 В какой день месяца? (введи число от 1 до 28):")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=day, callback_data=f"rday:{i}") for i, day in enumerate(WEEKDAYS[:4])],
            [InlineKeyboardButton(text=day, callback_data=f"rday:{i+4}") for i, day in enumerate(WEEKDAYS[4:])],
        ])
        await call.message.edit_text("📆 В какой день недели?", reply_markup=kb)


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
    await message.answer("🔔 Когда напомнить?", reply_markup=kb)


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

    await call.message.edit_text(
        f"✅ <b>{data['name']}</b> добавлен в регулярные платежи!\n\n"
        f"Буду напоминать {'в день платежа' if remind == 0 else f'за {remind} д.'}.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗓 Мой календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ])
    )
