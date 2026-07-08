import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import execute, fetchall, fetchone, get_todays_reminders, touch_reminder_sent
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from html import escape

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


def _rub(value) -> str:
    return f"{float(value or 0):,.0f}".replace(",", " ") + " ₽"


async def _trial_stats(user_id: int) -> dict:
    total = await fetchone(
        """SELECT
              COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0),
              COUNT(*)
           FROM transactions
           WHERE user_id=%s""",
        (user_id,),
    )
    top = await fetchall(
        """SELECT c.name, SUM(t.amount) AS total
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.type='expense'
           GROUP BY c.name
           ORDER BY total DESC
           LIMIT 3""",
        (user_id,),
    )
    fixed = await fetchone(
        """SELECT COALESCE(SUM(amount), 0)
           FROM transactions
           WHERE user_id=%s AND type='expense' AND kind='fixed'""",
        (user_id,),
    )
    return {
        "income": float(total[0]) if total else 0.0,
        "expense": float(total[1]) if total else 0.0,
        "count": int(total[2]) if total else 0,
        "top": top or [],
        "fixed": float(fixed[0]) if fixed else 0.0,
    }


def _top_lines(stats: dict) -> str:
    if not stats["top"]:
        return "Пока мало данных по категориям."
    return "\n".join(
        f"• {escape(str(name or 'Без категории'))} — {_rub(total)}"
        for name, total in stats["top"]
    )


def _trial_message(day: int, stats: dict) -> str:
    if stats["count"] == 0:
        return (
            "🌱 <b>День " + str(day) + " пробного периода</b>\n\n"
            "<blockquote>Финансовая ясность начинается не с идеальной таблицы, а с пары честных записей.</blockquote>\n\n"
            "Пока у меня мало данных, чтобы увидеть закономерности. Запиши несколько трат или доходов текстом/голосом — "
            "и я начну собирать для тебя понятную картину."
        )

    balance = stats["income"] - stats["expense"]
    if day == 3:
        return (
            "✨ <b>День 3: уже видна первая картина</b>\n\n"
            "<blockquote>Деньги любят ясность, а не напряжение.</blockquote>\n\n"
            f"Я уже вижу <b>{stats['count']}</b> операций.\n"
            f"💰 Доходы: <b>{_rub(stats['income'])}</b>\n"
            f"💸 Расходы: <b>{_rub(stats['expense'])}</b>\n"
            f"⚖️ Баланс: <b>{_rub(balance)}</b>\n\n"
            "🔎 Больше всего сейчас уходит сюда:\n"
            + _top_lines(stats)
            + "\n\nПродолжай просто записывать операции. Через несколько дней я смогу собрать уже более цельный бюджет и подсветить закономерности."
        )
    if day == 7:
        daily_avg = stats["expense"] / 7
        monthly_forecast = daily_avg * 30
        return (
            "📍 <b>День 7: появился недельный ритм</b>\n\n"
            "<blockquote>Неделя данных — уже не шум, а первые привычки в цифрах.</blockquote>\n\n"
            f"За неделю расходов: <b>{_rub(stats['expense'])}</b>\n"
            f"Средний расход в день: <b>{_rub(daily_avg)}</b>\n"
            f"Прогноз на месяц при таком темпе: <b>{_rub(monthly_forecast)}</b>\n\n"
            "Главные статьи расходов:\n"
            + _top_lines(stats)
            + "\n\nМожно открыть ИИ-помощник и написать: «Собери мне плановый бюджет на месяц по моим данным»."
        )
    return (
        "🏁 <b>День 10: пробный период подходит к концу</b>\n\n"
        "<blockquote>Лучший бюджет — тот, который помогает принимать решения без паники.</blockquote>\n\n"
        f"За пробный период записано операций: <b>{stats['count']}</b>.\n"
        f"💰 Доходы: <b>{_rub(stats['income'])}</b>\n"
        f"💸 Расходы: <b>{_rub(stats['expense'])}</b>\n"
        f"⚖️ Итоговый баланс по данным: <b>{_rub(balance)}</b>\n\n"
        "Самые заметные направления расходов:\n"
        + _top_lines(stats)
        + "\n\nЕсли бот помог увидеть картину, можно продлить доступ в тарифах."
    )


async def send_trial_journey_messages(bot):
    try:
        rows = await fetchall(
            """SELECT id, (CURRENT_DATE - created_at::date) AS age_days
               FROM users
               WHERE created_at IS NOT NULL
                 AND subscription_tier = 'premium'
                 AND premium_until IS NOT NULL
                 AND (CURRENT_DATE - created_at::date) BETWEEN 3 AND 10"""
        )
        for user_id, age_days in rows:
            day = int(age_days)
            if day not in (3, 7, 10):
                continue
            already_sent = await fetchone(
                "SELECT 1 FROM trial_journey_messages WHERE user_id=%s AND day=%s",
                (user_id, day),
            )
            if already_sent:
                continue

            stats = await _trial_stats(user_id)
            text = _trial_message(day, stats)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 ИИ-помощник", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="⭐ Тарифы", callback_data="premium")],
            ])
            try:
                await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
                await execute(
                    "INSERT INTO trial_journey_messages (user_id, day) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, day),
                )
            except Exception as e:
                logger.error(f"Failed to send trial journey message to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Trial journey scheduler error: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_reminders, "cron", hour=9, minute=0, args=[bot])
    scheduler.add_job(send_trial_journey_messages, "cron", hour=10, minute=0, args=[bot])
    scheduler.start()
    return scheduler
