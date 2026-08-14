from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.database import get_subscription_tier, get_promo, use_promo, activate_stars_payment, add_ai_usage_boost

router = Router()

# Звёзды (округление вниз, курс ~1.48 руб/звезда)
SCAN_TEXT_STARS = 100
BASE_STARS = 196
PREMIUM_STARS = 541
BOOSTER_STARS = 70
BOOSTER_PRICE = 10000
BOOSTER_MESSAGES = 30

# Цены в рублях (копейки для ЮКассы), копейки всегда округляем вниз
PRICES = {
    'scan_text': {1: 14900, 3: 44700, 6: 80400},   # 149*6=894, -10% = 804.6 -> 80400 (округлено вниз)
    'base':      {1: 29000, 3: 87000, 6: 156600},  # 290*6=1740, -10% = 1566 -> 156600
    'premium':   {1: 80000, 3: 240000, 6: 432000}, # 800*6=4800, -10% = 4320 -> 432000
}

PERIOD_LABELS = {
    1: '1 месяц',
    3: '3 месяца',
    6: '6 месяцев (-10%)',
}

TIER_NAMES = {
    'scan_text': 'Скан и текст',
    'base': 'База',
    'premium': 'Премиум',
}

TIER_STARS = {
    'scan_text': SCAN_TEXT_STARS,
    'base': BASE_STARS,
    'premium': PREMIUM_STARS,
}

# Тарифы, к которым применим Бустер
BOOSTER_ELIGIBLE_TIERS = {'scan_text', 'base'}


class PromoState(StatesGroup):
    waiting_code = State()


class DonateState(StatesGroup):
    waiting_amount = State()


def premium_keyboard(tier):
    buttons = []
    if tier == 'free':
        buttons = [
            [InlineKeyboardButton(text="📝 Скан и текст — 149 руб (100 ⭐)", callback_data="tier_scan_text")],
            [InlineKeyboardButton(text="⭐ База — 290 руб (196 ⭐)", callback_data="tier_base")],
            [InlineKeyboardButton(text="💎 Премиум — 800 руб (541 ⭐)", callback_data="tier_premium")],
            [InlineKeyboardButton(text=f"⭐ Бустер +{BOOSTER_MESSAGES} сообщений — 100 руб / 70 ⭐", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'scan_text':
        buttons = [
            [InlineKeyboardButton(text="📝 Продлить Скан и текст", callback_data="tier_scan_text")],
            [InlineKeyboardButton(text="⭐ База — 290 руб (196 ⭐)", callback_data="tier_base")],
            [InlineKeyboardButton(text="💎 Премиум — 800 руб (541 ⭐)", callback_data="tier_premium")],
            [InlineKeyboardButton(text=f"⭐ Бустер +{BOOSTER_MESSAGES} сообщений — 100 руб / 70 ⭐", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'base':
        buttons = [
            [InlineKeyboardButton(text="⭐ Продлить Базу", callback_data="tier_base")],
            [InlineKeyboardButton(text="📝 Скан и текст после Базы", callback_data="tier_scan_text")],
            [InlineKeyboardButton(text="💎 Премиум — 800 руб (541 ⭐)", callback_data="tier_premium")],
            [InlineKeyboardButton(text=f"⭐ Бустер +{BOOSTER_MESSAGES} сообщений — 100 руб / 70 ⭐", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'premium':
        buttons = [
            [InlineKeyboardButton(text="💎 Продлить Премиум", callback_data="tier_premium")],
            [InlineKeyboardButton(text="⭐ База после Премиума", callback_data="tier_base")],
            [InlineKeyboardButton(text="📝 Скан и текст после Премиума", callback_data="tier_scan_text")],
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
    base_stars = TIER_STARS[tier]
    if months == 1:
        stars = base_stars
    elif months == 3:
        stars = base_stars * 3
    else:
        stars = int(base_stars * 6 * 0.9)
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


def booster_payment_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Картой — 100 руб", callback_data="pay_booster_rub")],
        [InlineKeyboardButton(text=f"⭐ Звёздами Telegram ({BOOSTER_STARS} ⭐)", callback_data="pay_booster_stars")],
        [InlineKeyboardButton(text="Назад", callback_data="premium")],
    ])


def _date_label(value) -> str:
    return value.strftime("%d.%m.%Y") if value else "позже"


def _payment_success_text(tier_name: str, months: int, activation: dict) -> str:
    status = activation.get("status")
    until = _date_label(activation.get("until"))
    starts_at = _date_label(activation.get("starts_at"))
    if status == "queued":
        return (
            f"Оплата прошла! Тариф {tier_name} на {months} мес. поставил в очередь.\n\n"
            f"Он стартует {starts_at}, когда закончится текущий тариф, и будет работать до {until}. "
            "Дни не сгорают. Наконец-то деньги ведут себя прилично."
        )
    if status == "extended":
        return f"Оплата прошла! Тариф {tier_name} продлён до {until}. Дни не сгорели, всё по-взрослому."
    return f"Оплата прошла! Тариф {tier_name} активирован до {until}. Спасибо!"


@router.callback_query(F.data == "premium")
async def cb_premium(call: CallbackQuery):
    tier = await get_subscription_tier(call.from_user.id)

    if tier == 'free':
        text = (
            "Тарифы Баланс\n\n"
            "Бесплатный (сейчас у тебя)\n"
            "10 дней Премиума бесплатно при регистрации\n"
            "После окончания: только Последние, Отчёты и Заметки\n\n"
            "Скан и текст — 149 руб/мес (100 ⭐)\n"
            "Быстрый ввод текстом, сканирование чеков\n"
            "Отчёты ДДС, платёжный календарь\n"
            "ИИ-ассистент (60 сообщений/мес)\n"
            "Без голосового ввода\n\n"
            "База — 290 руб/мес (196 ⭐)\n"
            "Всё из Скан и текст + голосовой ввод\n"
            "ИИ-ассистент (60 сообщений/мес)\n\n"
            "Премиум — 800 руб/мес (541 ⭐)\n"
            "Всё из База +\n"
            "ИИ-ассистент безлимит\n"
            "Свободные беседы с ИИ на любые темы\n"
            "Выгрузка в Excel\n\n"
            "Бустер — 100 руб (70 ⭐)\n"
            f"+{BOOSTER_MESSAGES} сообщений ИИ разово (для тарифов Скан и текст / База)\n\n"
            "Оплата картой или звёздами Telegram"
        )
    elif tier == 'scan_text':
        text = (
            "У тебя активен тариф Скан и текст!\n\n"
            "Доступно: текстовый ввод, чеки, отчёты, ИИ (60 сообщений/мес)\n"
            "Голосовой ввод недоступен на этом тарифе\n\n"
            "Можно продлить заранее — оплаченные дни не сгорят.\n\n"
            "Upgrade до Премиум — голос, безлимитный ИИ, свободные беседы + Excel за 800 руб/мес\n\n"
            f"Или купи Бустер +{BOOSTER_MESSAGES} сообщений за 100 руб"
        )
    elif tier == 'base':
        text = (
            "У тебя активен тариф База!\n\n"
            "Доступно: голос, чеки, отчёты, ИИ (60 сообщений/мес)\n\n"
            "Можно продлить заранее или поставить тариф ниже следующим — дни не сгорят.\n\n"
            "Upgrade до Премиум — безлимитный ИИ, свободные беседы + Excel за 800 руб/мес\n\n"
            f"Или купи Бустер +{BOOSTER_MESSAGES} сообщений за 100 руб"
        )
    elif tier == 'premium':
        text = (
            "У тебя активен Премиум!\n\n"
            "Доступно всё включая безлимитный ИИ, свободные беседы на любые темы и Excel.\n"
            "Можно продлить заранее или поставить следующий тариф после Премиума.\n"
            "Спасибо за поддержку!"
        )
    else:
        text = (
            "У тебя активен максимальный тариф!\n\n"
            "Доступны все функции бота.\n"
            "Спасибо за поддержку!"
        )

    await call.message.edit_text(text, parse_mode=None, reply_markup=premium_keyboard(tier))


@router.callback_query(F.data.in_({"tier_scan_text", "tier_base", "tier_premium"}))
async def cb_tier_select(call: CallbackQuery):
    tier = call.data.replace("tier_", "")
    name = TIER_NAMES[tier]
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

    base_stars = TIER_STARS[tier]
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
    from app.database import get_subscription_tier
    tier = await get_subscription_tier(call.from_user.id)
    if tier not in BOOSTER_ELIGIBLE_TIERS:
        await call.answer(
            "Бустер доступен только для тарифов Скан и текст / База.",
            show_alert=True
        )
        return
    await call.message.edit_text(
        f"Бустер +{BOOSTER_MESSAGES} сообщений ИИ\nСтоимость: 100 руб или {BOOSTER_STARS} ⭐\n\nВыбери способ оплаты:",
        parse_mode=None,
        reply_markup=booster_payment_keyboard()
    )
    await call.answer()


@router.callback_query(F.data == "pay_booster_rub")
async def cb_pay_booster_rub(call: CallbackQuery):
    import os, uuid
    from yookassa import Configuration, Payment
    from app.database import get_subscription_tier

    tier = await get_subscription_tier(call.from_user.id)
    if tier not in BOOSTER_ELIGIBLE_TIERS:
        await call.answer(
            "Бустер доступен только для тарифов Скан и текст / База.",
            show_alert=True
        )
        return

    Configuration.account_id = os.environ.get("YOOKASSA_SHOP_ID")
    Configuration.secret_key = os.environ.get("YOOKASSA_SECRET_KEY")

    try:
        payment = Payment.create({
            "amount": {"value": f"{BOOSTER_PRICE / 100:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/Balansfinansbot"},
            "capture": True,
            "description": f"Баланс бот — Бустер +{BOOSTER_MESSAGES} сообщений",
            "receipt": {
                "customer": {"email": "noreply@balansbot.ru"},
                "items": [{
                    "description": f"Бустер +{BOOSTER_MESSAGES} сообщений ИИ",
                    "quantity": "1.00",
                    "amount": {"value": f"{BOOSTER_PRICE / 100:.2f}", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }]
            },
            "metadata": {
                "kind": "booster",
                "user_id": str(call.from_user.id),
                "messages": BOOSTER_MESSAGES,
            }
        }, str(uuid.uuid4()))

        url = payment.confirmation.confirmation_url
        await call.message.edit_text(
            f"Оплата бустера +{BOOSTER_MESSAGES} сообщений\n100 руб\n\nНажми кнопку для оплаты картой:",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить картой", url=url)],
                [InlineKeyboardButton(text="Назад", callback_data="buy_booster")],
            ])
        )
    except Exception as e:
        await call.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data == "pay_booster_stars")
async def cb_pay_booster_stars(call: CallbackQuery):
    from app.database import get_subscription_tier
    tier = await get_subscription_tier(call.from_user.id)
    if tier not in BOOSTER_ELIGIBLE_TIERS:
        await call.answer(
            "Бустер доступен только для тарифов Скан и текст / База.",
            show_alert=True
        )
        return
    await call.message.answer_invoice(
        title=f"Бустер +{BOOSTER_MESSAGES} сообщений",
        description=f"Разовое пополнение: +{BOOSTER_MESSAGES} сообщений ИИ-ассистента",
        payload="booster_30",
        currency="XTR",
        prices=[LabeledPrice(label=f"Бустер +{BOOSTER_MESSAGES} сообщений (~100 руб)", amount=BOOSTER_STARS)],
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

    if payload in {"booster_30", "booster_80"}:
        await add_ai_usage_boost(message.from_user.id, BOOSTER_MESSAGES)
        await message.answer(
            f"Бустер активирован! +{BOOSTER_MESSAGES} сообщений ИИ добавлено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
        return

    # Парсим tier и months из payload например "base_3mo" или "premium_1mo"
    parts = payload.replace("mo", "").split("_")
    tier = "_".join(parts[:-1])
    months = int(parts[-1])
    days = months * 30

    activation = await activate_stars_payment(message.from_user.id, tier=tier, days=days)
    tier_name = TIER_NAMES.get(tier, tier)
    await message.answer(
        _payment_success_text(tier_name, months, activation),
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

    if max_uses is not None and used_count >= max_uses:
        await message.answer("Промокод уже использован максимальное количество раз.")
        return

    if expires_at:
        from datetime import datetime
        if datetime.now() > expires_at:
            await message.answer("Срок действия промокода истёк.")
            return

    success = await use_promo(message.from_user.id, promo_id, tier, days)
    if success:
        tier_name = TIER_NAMES.get(tier, tier)
        await message.answer(
            f"Промокод активирован! {tier_name} на {days} дней — готово!",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
    else:
        await message.answer("Ты уже использовал этот промокод.")
