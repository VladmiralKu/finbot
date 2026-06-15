import asyncio
import json
import logging
import os
from aiohttp import web
from app.database import activate_stars_payment, fetchone, execute

logger = logging.getLogger(__name__)

async def yookassa_webhook(request):
    try:
        data = await request.json()
        event = data.get("event", "")
        obj = data.get("object", {})

        if event == "payment.succeeded":
            metadata = obj.get("metadata", {})
            user_id = metadata.get("user_id")
            tier = metadata.get("tier")
            months = int(metadata.get("months", 1))

            if user_id and tier:
                days = months * 30
                await activate_stars_payment(int(user_id), tier=tier, days=days)
                logger.info(f"YooKassa: activated {tier} {months}mo for user {user_id}")

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
