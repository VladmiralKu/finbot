from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.database import get_subscription_tier, get_promo, use_promo, activate_stars_payment

router = Router()

# Звёзды
START_STARS = 100
PREMIUM_STARS = 400
BOOSTER_STARS = 70

# Цены в рублях (копейки для ЮКассы)
PRICES = {
    'start': {1: 14900, 3: 44700, 6: 80460},   # 6 мес = 149*6*0.9
    'premium': {1: 59000, 3: 177000, 6: 318600}, # 6 мес = 590*6*0.9
}

PERIOD_LABELS = {
    1: '1 месяц',
    3: '3 месяца',
    6: '6 месяцев (-10%)',
}

TIER_NAMES = {
    'start': 'Старт',
    'premium': 'Премиум',
}


class PromoState(StatesGroup):
    waiting_code = State()


class DonateState(StatesGroup):
    waiting_amount = State()


def premium_keyboard(tier):
    buttons = []
    if tier == 'free':
        buttons = [
            [InlineKeyboardButton(text="⭐ Старт — 100 звёзд (~149 руб)", callback_data="tier_start")],
            [InlineKeyboardButton(text="⭐ Премиум — 400 звёзд (~590 руб)", callback_data="tier_premium")],
            [InlineKeyboardButton(text="⭐ Бустер +80 сообщений — 70 звёзд (~100 руб)", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'start':
        buttons = [
            [InlineKeyboardButton(text="⭐ Премиум — 400 звёзд (~590 руб)", callback_data="tier_premium")],
            [InlineKeyboardButton(text="⭐ Бустер +80 сообщений — 70 звёзд (~100 руб)", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'premium':
        buttons = [
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def period_keyboard(tier):
    p = PRICES[tier]
    buttons = [
        [InlineKeyboardButton(
            text=f"1 месяц — {p[1]//100} руб",
            callback_data=f"period:{tier}:1"
        )],
        [InlineKeyboardButton(
            text=f"3 месяца — {p[3]//100} руб",
            callback_data=f"period:{tier}:3"
        )],
        [InlineKeyboardButton(
            text=f"6 месяцев — {p[6]//100} руб (-10%)",
            callback_data=f"period:{tier}:6"
        )],
        [InlineKeyboardButton(text="Назад", callback_data="premium")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(tier, months):
    stars = START_STARS if tier == 'start' else PREMIUM_STARS
    if months == 3:
        stars = stars * 3
    elif months == 6:
        stars = int(stars * 6 * 0.9)
    buttons = [
        [InlineKeyboardButton(
            text=f"💳 Картой (ЮКасса)",
            callback_data=f"pay_rub:{tier}:{months}"
        )],
        [InlineKeyboardButton(
            text=f"⭐ Звёздами Telegram ({stars} ⭐)",
            callback_data=f"pay_stars:{tier}:{months}"
        )],
        [InlineKeyboardButton(text="Назад", callback_data=f"tier_{tier}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "premium")
async def cb_premium(call: CallbackQuery):
    tier = await get_subscription_tier(call.from_user.id)

    if tier == 'free':
        text = (
            "Тарифы Баланс\n\n"
            "Бесплатный (сейчас у тебя)\n"
            "3 дня Премиума бесплатно при регистрации\n"
            "После окончания: только Последние, Отчёты и Заметки\n\n"
            "Старт — 149 руб/мес (100 ⭐)\n"
            "Быстрый ввод, голосовой ввод\n"
            "Отчёты ДДС, платёжный календарь\n"
            "Сканирование чеков\n"
            "ИИ-ассистент (60 сообщений/мес)\n\n"
            "Премиум — 590 руб/мес (400 ⭐)\n"
            "Всё из Старт +\n"
            "ИИ-ассистент безлимит\n"
            "Выгрузка в Excel\n\n"
            "Бустер — 100 руб (70 ⭐)\n"
            "+80 сообщений ИИ разово\n\n"
            "Оплата картой или звёздами Telegram"
        )
    elif tier == 'start':
        text = (
            "У тебя активен тариф Старт!\n\n"
            "Доступно: голос, чеки, отчёты, ИИ (60 сообщений/мес)\n\n"
            "Upgrade до Премиум — безлимитный ИИ + Excel за 590 руб/мес\n\n"
            "Или купи Бустер +80 сообщений за 100 руб"
        )
    elif tier == 'premium':
        text = (
            "У тебя активен Премиум!\n\n"
            "Доступно всё включая безлимитный ИИ и Excel.\n"
            "Спасибо за поддержку!"
        )
    else:
        text = (
            "У тебя активен максимальный тариф!\n\n"
            "Доступны все функции бота.\n"
            "Спасибо за поддержку!"
        )

    await call.message.edit_text(text, parse_mode=None, reply_markup=premium_keyboard(tier))


@router.callback_query(F.data.in_({"tier_start", "tier_premium"}))
async def cb_tier_select(call: CallbackQuery):
    tier = call.data.replace("tier_", "")
    name = TIER_NAMES[tier]
    price1 = PRICES[tier][1] // 100
    await call.message.edit_text(
        f"Тариф {name}\n\nВыбери период подписки:",
        parse_mode=None,
        reply_markup=period_keyboard(tier)
    )


@router.callback_query(F.data.startswith("period:"))
async def cb_period_select(call: CallbackQuery):
    _, tier, months_str = call.data.split(":")
    months = int(months_str)
    name = TIER_NAMES[tier]
    price = PRICES[tier][months] // 100
    label = PERIOD_LABELS[months]
    await call.message.edit_text(
        f"Тариф {name} — {label}\nСтоимость: {price} руб\n\nВыбери способ оплаты:",
        parse_mode=None,
        reply_markup=payment_keyboard(tier, months)
    )


@router.callback_query(F.data.startswith("pay_rub:"))
async def cb_pay_rub(call: CallbackQuery):
    import os, uuid
    from yookassa import Configuration, Payment

    _, tier, months_str = call.data.split(":")
    months = int(months_str)
    amount = PRICES[tier][months]
    name = TIER_NAMES[tier]
    label = PERIOD_LABELS[months]

    Configuration.account_id = os.environ.get("YOOKASSA_SHOP_ID")
    Configuration.secret_key = os.environ.get("YOOKASSA_SECRET_KEY")

    try:
        payment = Payment.create({
            "amount": {"value": f"{amount / 100:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/Balansfinansbot"},
            "capture": True,
            "description": f"Баланс бот — {name} {label}",
            "receipt": {
                "customer": {"email": "noreply@balansbot.ru"},
                "items": [{
                    "description": f"{name} {label}",
                    "quantity": "1.00",
                    "amount": {"value": f"{amount / 100:.2f}", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }]
            },
            "metadata": {
                "user_id": str(call.from_user.id),
                "tier": tier,
                "months": months,
            }
        }, str(uuid.uuid4()))

        url = payment.confirmation.confirmation_url
        await call.message.edit_text(
            f"Оплата {name} — {label}\n{amount // 100} руб\n\nНажми кнопку для оплаты картой:",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить картой", url=url)],
                [InlineKeyboardButton(text="Назад", callback_data=f"period:{tier}:{months}")],
            ])
        )
    except Exception as e:
        await call.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data.startswith("pay_stars:"))
async def cb_pay_stars(call: CallbackQuery):
    _, tier, months_str = call.data.split(":")
    months = int(months_str)
    name = TIER_NAMES[tier]
    label = PERIOD_LABELS[months]

    base_stars = START_STARS if tier == 'start' else PREMIUM_STARS
    if months == 1:
        stars = base_stars
    elif months == 3:
        stars = base_stars * 3
    else:
        stars = int(base_stars * 6 * 0.9)

    await call.message.answer_invoice(
        title=f"Тариф {name} — {label}",
        description=f"{label} подписки {name}: все функции включены",
        payload=f"{tier}_{months}mo",
        currency="XTR",
        prices=[LabeledPrice(label=f"{name} {label}", amount=stars)],
    )
    await call.answer()


@router.callback_query(F.data == "buy_booster")
async def cb_buy_booster(call: CallbackQuery):
    await call.message.answer_invoice(
        title="Бустер +80 сообщений",
        description="Разовое пополнение: +80 сообщений ИИ-ассистента",
        payload="booster_80",
        currency="XTR",
        prices=[LabeledPrice(label="Бустер +80 сообщений (~100 руб)", amount=BOOSTER_STARS)],
    )
    await call.answer()


@router.callback_query(F.data == "donate")
async def cb_donate(call: CallbackQuery, state: FSMContext):
    await state.set_state(DonateState.waiting_amount)
    await call.message.edit_text(
        "Выбери сумму доната в звёздах:\n\n1 звезда ≈ 1.5 руб",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="50 ⭐ (~75 руб)", callback_data="donate_50")],
            [InlineKeyboardButton(text="100 ⭐ (~150 руб)", callback_data="donate_100")],
            [InlineKeyboardButton(text="300 ⭐ (~450 руб)", callback_data="donate_300")],
            [InlineKeyboardButton(text="Отмена", callback_data="premium")],
        ])
    )


@router.callback_query(F.data.startswith("donate_"))
async def cb_donate_amount(call: CallbackQuery, state: FSMContext):
    await state.clear()
    amount = int(call.data.split("_")[1])
    await call.message.answer_invoice(
        title="Поддержать проект",
        description="Донат разработчику бота Баланс. Спасибо за поддержку!",
        payload=f"donate_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"Донат {amount} звёзд", amount=amount)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload

    if payload.startswith("donate_"):
        stars = message.successful_payment.total_amount
        await message.answer(
            f"Спасибо за поддержку! Получено {stars} звёзд 💝",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
        return

    if payload == "booster_80":
        from app.database import execute
        await execute(
            """INSERT INTO ai_usage_boost (user_id, messages_added, created_at)
               VALUES (%s, 80, NOW())
               ON CONFLICT (user_id) DO UPDATE
               SET messages_added = ai_usage_boost.messages_added + 80""",
            (message.from_user.id,)
        )
        await message.answer(
            "Бустер активирован! +80 сообщений ИИ добавлено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
        return

    # Парсим tier и months из payload например "start_3mo" или "premium_1mo"
    parts = payload.replace("mo", "").split("_")
    tier = parts[0]
    months = int(parts[1]) if len(parts) > 1 else 1
    days = months * 30

    await activate_stars_payment(message.from_user.id, tier=tier, days=days)
    tier_name = "Премиум" if tier == "premium" else "Старт"
    await message.answer(
        f"Оплата прошла! Тариф {tier_name} активирован на {months} мес. Спасибо!",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
        ])
    )


@router.callback_query(F.data == "enter_promo")
async def cb_enter_promo(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoState.waiting_code)
    await call.message.edit_text(
        "Введи промокод:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="premium")]
        ])
    )


@router.message(PromoState.waiting_code)
async def msg_promo_code(message: Message, state: FSMContext):
    await state.clear()
    code = message.text.strip().upper()
    promo = await get_promo(code)

    if not promo:
        await message.answer(
            "Промокод не найден.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Попробовать снова", callback_data="enter_promo")],
                [InlineKeyboardButton(text="Назад", callback_data="premium")],
            ])
        )
        return

    promo_id, _, tier, days, max_uses, used_count, expires_at = promo

    if used_count >= max_uses:
        await message.answer("Промокод уже использован максимальное количество раз.")
        return

    if expires_at:
        from datetime import datetime
        if datetime.now() > expires_at:
            await message.answer("Срок действия промокода истёк.")
            return

    success = await use_promo(message.from_user.id, promo_id, tier, days)
    if success:
        tier_name = "Премиум" if tier == "premium" else "Старт"
        await message.answer(
            f"Промокод активирован! {tier_name} на {days} дней — готово!",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
    else:
        await message.answer("Ты уже использовал этот промокод.")
