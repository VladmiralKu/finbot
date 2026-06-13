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
START_STARS = 100      # ~149 руб
PREMIUM_STARS = 400    # ~590 руб
BOOSTER_STARS = 70     # ~100 руб


class PromoState(StatesGroup):
    waiting_code = State()


class DonateState(StatesGroup):
    waiting_amount = State()


def premium_keyboard(tier):
    buttons = []
    if tier == 'free':
        buttons = [
            [InlineKeyboardButton(text="⭐ Старт — 100 звёзд (~149 руб)", callback_data="buy_start")],
            [InlineKeyboardButton(text="⭐ Премиум — 400 звёзд (~590 руб)", callback_data="buy_premium")],
            [InlineKeyboardButton(text="⭐ Бустер +80 сообщений — 70 звёзд (~100 руб)", callback_data="buy_booster")],
            [InlineKeyboardButton(text="💝 Поддержать проект", callback_data="donate")],
            [InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    elif tier == 'start':
        buttons = [
            [InlineKeyboardButton(text="⭐ Премиум — 400 звёзд (~590 руб)", callback_data="buy_premium")],
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
            "Оплата звёздами Telegram"
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


@router.callback_query(F.data == "buy_start")
async def cb_buy_start(call: CallbackQuery):
    await call.message.answer_invoice(
        title="Тариф Старт",
        description="30 дней: голосовой ввод, чеки, отчёты, ИИ-ассистент 60 сообщений/мес",
        payload="start_30",
        currency="XTR",
        prices=[LabeledPrice(label="Старт 30 дней (~149 руб)", amount=START_STARS)],
    )
    await call.answer()


@router.callback_query(F.data == "buy_premium")
async def cb_buy_premium(call: CallbackQuery):
    await call.message.answer_invoice(
        title="Тариф Премиум",
        description="30 дней: всё включено + безлимитный ИИ-ассистент + выгрузка Excel",
        payload="premium_30",
        currency="XTR",
        prices=[LabeledPrice(label="Премиум 30 дней (~590 руб)", amount=PREMIUM_STARS)],
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
        "Введи сумму доната в звёздах (например: 50, 100, 500):\n\n"
        "1 звезда ≈ 1.5 руб",
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
    from app.database import execute, fetchone

    if payload.startswith("donate_"):
        stars = message.successful_payment.total_amount
        await message.answer(
            f"Спасибо за поддержку! Получено {stars} звёзд 💝\n"
            "Это очень важно для развития проекта!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
        return

    if payload == "booster_80":
        # Добавляем 80 сообщений к лимиту
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

    tier = "premium" if "premium" in payload else "start"
    await activate_stars_payment(message.from_user.id, tier=tier, days=30)
    tier_name = "Премиум" if tier == "premium" else "Старт"
    await message.answer(
        f"Оплата прошла! Тариф {tier_name} активирован на 30 дней. Спасибо!",
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
