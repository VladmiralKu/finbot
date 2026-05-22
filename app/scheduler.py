import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import get_todays_reminders, mark_reminder_sent

logger = logging.getLogger(__name__)


async def send_reminders(bot):
    try:
        payments = await get_todays_reminders()
        for p in payments:
            user_id = p[-1]
            name = p[2]
            amount = float(p[3])
            next_date = p[14]
            from datetime import date
            days_left = (next_date - date.today()).days

            if days_left == 0:
                text = f"🔴 <b>Сегодня платёж!</b>\n\n💸 {name} — {amount:,.0f} ₽\n\nНе забудь оплатить!"
            else:
                text = f"⚠️ <b>Через {days_left} дн. платёж</b>\n\n💸 {name} — {amount:,.0f} ₽\n📅 {next_date.strftime('%d.%m.%Y')}\n\nПроверь баланс заранее!"

            try:
                await bot.send_message(user_id, text, parse_mode="HTML")
                await mark_reminder_sent(p[0])
                logger.info(f"Reminder sent to {user_id} for {name}")
            except Exception as e:
                logger.error(f"Failed to send reminder to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Scheduler error: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_reminders, "cron", hour=9, minute=0, args=[bot])
    scheduler.start()
    return scheduler
