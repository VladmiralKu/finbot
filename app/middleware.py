import os
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable,
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            admin_ids = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x]
            user_id = event.from_user.id if event.from_user else 0

            # Админы работают всегда
            if user_id not in admin_ids:
                text = "🔧 <b>Технические работы</b>\n\nБот временно недоступен. Обычно это занимает 5-10 минут. Попробуй позже!"
                if isinstance(event, Message):
                    await event.answer(text, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🔧 Технические работы, скоро вернёмся!", show_alert=True)
                return
        return await handler(event, data)
