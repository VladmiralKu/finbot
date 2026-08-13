import asyncio
import json
import logging
import os
from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from yookassa import Configuration, Payment
from yookassa.domain.common import SecurityHelper
from app.database import activate_stars_payment, fetchone, execute

logger = logging.getLogger(__name__)

_bot = None

TIER_NAMES = {
    'scan_text': 'Скан и текст',
    'base': 'База',
    'premium': 'Премиум',
}


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


async def _ensure_yookassa_payments_table():
    await execute("""
        CREATE TABLE IF NOT EXISTS yookassa_payments (
            provider_payment_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            tier VARCHAR(50) NOT NULL,
            months INT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'succeeded',
            created_at TIMESTAMP DEFAULT NOW(),
            notified_at TIMESTAMP
        )
    """)


async def _claim_yookassa_payment(payment_id: str, user_id: int, tier: str, months: int) -> bool:
    await _ensure_yookassa_payments_table()
    row = await fetchone(
        """INSERT INTO yookassa_payments (provider_payment_id, user_id, tier, months)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (provider_payment_id) DO NOTHING
           RETURNING provider_payment_id""",
        (payment_id, user_id, tier, months),
    )
    return bool(row)


async def _notify_payment_success(user_id: int, tier: str, months: int, activation: dict, payment_id: str):
    if not _bot:
        logger.warning("YooKassa: bot instance is not configured, cannot notify user %s", user_id)
        return

    tier_name = TIER_NAMES.get(tier, tier)
    try:
        await _bot.send_message(
            user_id,
            _payment_success_text(tier_name, months, activation),
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ]),
        )
        await execute(
            "UPDATE yookassa_payments SET notified_at = NOW() WHERE provider_payment_id = %s",
            (payment_id,),
        )
    except Exception as e:
        logger.warning("YooKassa: failed to notify user %s about payment %s: %s", user_id, payment_id, e)


def _get_client_ip(request):
    # Railway и большинство прокси передают реальный IP через X-Forwarded-For
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote


async def yookassa_webhook(request):
    try:
        # Уровень 1: проверяем, что запрос пришёл с доверенного IP ЮКассы
        client_ip = _get_client_ip(request)
        try:
            ip_trusted = SecurityHelper().is_ip_trusted(client_ip)
        except Exception as e:
            logger.error(f"YooKassa IP check failed: {e}")
            ip_trusted = False

        if not ip_trusted:
            logger.warning(f"YooKassa webhook: untrusted IP {client_ip}, request rejected")
            return web.Response(status=400, text="Untrusted source")

        data = await request.json()
        event = data.get("event", "")
        obj = data.get("object", {})
        payment_id = obj.get("id")

        if event == "payment.succeeded" and payment_id:
            # Уровень 2: сверяем реальный статус платежа через API ЮКассы,
            # не доверяем напрямую содержимому уведомления
            Configuration.account_id = os.environ.get("YOOKASSA_SHOP_ID")
            Configuration.secret_key = os.environ.get("YOOKASSA_SECRET_KEY")

            real_payment = Payment.find_one(payment_id)
            if real_payment.status != "succeeded":
                logger.warning(f"YooKassa webhook: payment {payment_id} status mismatch ({real_payment.status})")
                return web.Response(status=200, text="OK")

            metadata = real_payment.metadata or {}
            user_id = metadata.get("user_id")
            tier = metadata.get("tier")
            months = int(metadata.get("months", 1))

            if user_id and tier:
                user_id = int(user_id)
                is_new_payment = await _claim_yookassa_payment(payment_id, user_id, tier, months)
                if not is_new_payment:
                    logger.info("YooKassa: payment %s already processed, skipping", payment_id)
                    return web.Response(status=200, text="OK")

                days = months * 30
                activation = await activate_stars_payment(user_id, tier=tier, days=days)
                await _notify_payment_success(user_id, tier, months, activation, payment_id)
                logger.info(
                    "YooKassa: %s %s %smo for user %s (payment %s verified)",
                    activation.get("status", "activated"),
                    tier,
                    months,
                    user_id,
                    payment_id,
                )

        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"YooKassa webhook error: {e}")
        return web.Response(status=200, text="OK")


async def healthcheck(request):
    return web.Response(text="OK")


async def start_webhook_server(bot=None):
    global _bot
    _bot = bot

    app = web.Application()
    app.router.add_post("/yookassa/webhook", yookassa_webhook)
    app.router.add_get("/health", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")
