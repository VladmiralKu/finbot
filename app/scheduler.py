import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import get_todays_reminders, touch_reminder_sent
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


async def send_reminders(bot):
    try:
        payments = await get_todays_reminders()
        for p in payments:
            payment_id = p[0]
            user_id = p[1]
            name = p[2]
            amount = float(p[3])
            next_date = p[14]
            from datetime import date
            days_left = (next_date - date.today()).days

            if days_left <= 0:
                title = "Сегодня платёж!" if days_left == 0 else "Платёж просрочен"
                text = (f"💸 <b>{title}</b>\n\n"
                        f"📌 {name} — {amount:,.0f} ₽\n\n"
                        f"Напомнил. Транзакцию внеси отдельно после оплаты.")
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Уже оплатил", callback_data=f"reminder_ack:{payment_id}"),
                        InlineKeyboardButton(text="❌ Нет", callback_data=f"postpone:{payment_id}"),
                    ]
                ])
                try:
                    await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
                    await touch_reminder_sent(payment_id)
                except Exception as e:
                    logger.error(f"Failed to send reminder to {user_id}: {e}")
            else:
                text = (f"⚠️ <b>Через {days_left} дн. платёж</b>\n\n"
                        f"💸 {name} — {amount:,.0f} ₽\n"
                        f"📅 {next_date.strftime('%d.%m.%Y')}\n\n"
                        f"Проверь баланс заранее!")
                try:
                    await bot.send_message(user_id, text, parse_mode="HTML")
                    await touch_reminder_sent(payment_id)
                except Exception as e:
                    logger.error(f"Failed to send reminder to {user_id}: {e}")

    except Exception as e:
        logger.error(f"Scheduler error: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_reminders, "cron", hour=9, minute=0, args=[bot])
    scheduler.start()
    return scheduler
