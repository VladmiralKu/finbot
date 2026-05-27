import asyncio
import logging
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers.main import router as main_router
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

    dp.include_router(main_router)
    dp.include_router(recurring_router)
    dp.include_router(premium_router)

    await get_pool()
    logger.info("Database pool initialized")

    scheduler = setup_scheduler(bot)

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
