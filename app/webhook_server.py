import asyncio
import json
import logging
import os
from aiohttp import web
from yookassa import Configuration, Payment
from yookassa.domain.common import SecurityHelper
from app.database import activate_stars_payment, fetchone, execute

logger = logging.getLogger(__name__)


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
                days = months * 30
                activation = await activate_stars_payment(int(user_id), tier=tier, days=days)
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


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/yookassa/webhook", yookassa_webhook)
    app.router.add_get("/health", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")
