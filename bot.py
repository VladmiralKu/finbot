import asyncio
import logging
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers.admin import router as admin_router
from app.handlers.main import router as main_router
from app.handlers.business import router as business_router
from app.handlers.ai_assistant import router as ai_router
from app.handlers.receipt import router as receipt_router
from app.handlers.voice import router as voice_router
from app.handlers.recurring import router as recurring_router
from app.handlers.premium import router as premium_router
from app.database import get_pool, close_pool
from app.scheduler import setup_scheduler
from app.middleware import MaintenanceMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware техобслуживания
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())

    dp.include_router(admin_router)
    dp.include_router(main_router)
    dp.include_router(business_router)
    dp.include_router(ai_router)
    dp.include_router(receipt_router)
    dp.include_router(voice_router)
    dp.include_router(recurring_router)
    dp.include_router(premium_router)

    await get_pool()
    logger.info("Database pool initialized")

    # Миграция ai_history
    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS ai_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("CREATE INDEX IF NOT EXISTS idx_ai_history_user ON ai_history(user_id, created_at)")
    except Exception as e:
        logger.warning("Migration ai_history: " + str(e))

    scheduler = setup_scheduler(bot)

    from app.webhook_server import start_webhook_server
    await start_webhook_server()

    try:
        logger.info("Bot started")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await close_pool()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
