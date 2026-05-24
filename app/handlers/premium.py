from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.database import get_subscription_tier, get_promo, use_promo, activate_stars_payment
from app.keyboards import premium_keyboard

router = Router()

# Цены в Telegram Stars (XTR)
PREMIUM_STARS = 150
BUSINESS_STARS = 300


class PromoState(StatesGroup):
    waiting_code = State()


@router.callback_query(F.data == "premium")
async def cb_premium(call: CallbackQuery):
    tier = await get_subscription_tier(call.from_user.id)

    if tier == 'free':
        text = (
            "⭐ <b>Выбери тариф</b>\n\n"
            "🔹 <b>Premium — 150 ⭐/мес</b>\n"
            "• Неограниченные транзакции\n"
            "• 🤖 ИИ-анализ расходов\n"
            "• 📷 Сканирование чеков\n"
            "• 🗓 Платёжный календарь\n"
            "• Расширенные отчёты\n\n"
            "💼 <b>Business — 300 ⭐/мес</b>\n"
            "• Всё из Premium\n"
            "• Функции для бизнеса (скоро)\n\n"
            "💡 Оплата звёздами Telegram — быстро и безопасно"
        )
    elif tier == 'premium':
        text = "⭐ <b>У тебя активен Premium!</b>\n\nХочешь upgrade до Business?"
    else:
        text = "💼 <b>У тебя активен Premium Business!</b>"

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=premium_keyboard(tier))


# --- Оплата Stars ---

@router.callback_query(F.data == "buy_stars_premium")
async def cb_buy_premium(call: CallbackQuery):
    await call.message.answer_invoice(
        title="Premium подписка",
        description="30 дней Premium: ИИ-анализ, сканирование чеков, календарь платежей",
        payload="premium_30",
        currency="XTR",
        prices=[LabeledPrice(label="Premium 30 дней", amount=PREMIUM_STARS)],
    )
    await call.answer()


@router.callback_query(F.data == "buy_stars_business")
async def cb_buy_business(call: CallbackQuery):
    await call.message.answer_invoice(
        title="Premium Business",
        description="30 дней Premium Business — всё включено + бизнес-функции",
        payload="business_30",
        currency="XTR",
        prices=[LabeledPrice(label="Business 30 дней", amount=BUSINESS_STARS)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    tier = "business" if "business" in payload else "premium"
    await activate_stars_payment(message.from_user.id, tier=tier, days=30)
    await message.answer(
        f"✅ <b>Оплата прошла!</b>\n\n"
        f"{'💼 Premium Business' if tier == 'business' else '⭐ Premium'} активирован на 30 дней.\n\n"
        f"Спасибо! Enjoy 🚀",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
        ])
    )


# --- Промокоды ---

@router.callback_query(F.data == "enter_promo")
async def cb_enter_promo(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoState.waiting_code)
    await call.message.edit_text(
        "🎟 Введи промокод:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="premium")]
        ])
    )


@router.message(PromoState.waiting_code)
async def msg_promo_code(message: Message, state: FSMContext):
    await state.clear()
    code = message.text.strip().upper()
    promo = await get_promo(code)

    if not promo:
        await message.answer(
            "❌ Промокод не найден. Проверь правильность ввода.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎟 Попробовать снова", callback_data="enter_promo")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="premium")],
            ])
        )
        return

    promo_id, _, tier, days, max_uses, used_count, expires_at = promo

    # Проверка лимита
    if used_count >= max_uses:
        await message.answer("❌ Промокод уже использован максимальное количество раз.")
        return

    # Проверка срока
    if expires_at:
        from datetime import datetime
        if datetime.now() > expires_at:
            await message.answer("❌ Срок действия промокода истёк.")
            return

    success = await use_promo(message.from_user.id, promo_id, tier, days)
    if success:
        tier_name = "💼 Premium Business" if tier == "business" else "⭐ Premium"
        await message.answer(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"{tier_name} на {days} дней — активирован!\n\nПриятного пользования 🚀",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ])
        )
    else:
        await message.answer("❌ Ты уже использовал этот промокод.")
