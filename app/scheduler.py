import logging
import os
from datetime import date, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import execute, fetchall, fetchone, get_todays_reminders, touch_reminder_sent
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from html import escape

logger = logging.getLogger(__name__)


def _week_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


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


async def send_daily_activity_reminders(bot):
    try:
        rows = await fetchall(
            """SELECT u.id
               FROM users u
               WHERE COALESCE(u.subscription_tier, 'free') <> 'free'
                 AND (u.premium_until IS NULL OR u.premium_until > NOW())
                 AND NOT EXISTS (
                     SELECT 1 FROM transactions t
                     WHERE t.user_id = u.id AND t.transaction_date = CURRENT_DATE
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM daily_activity_reminders r
                     WHERE r.user_id = u.id AND r.reminder_date = CURRENT_DATE
                 )"""
        )
        for (user_id,) in rows:
            text = (
                "🌙 <b>Сегодня ещё пусто</b>\n\n"
                "<blockquote>Одна короткая запись сегодня — меньше тумана завтра.</blockquote>\n\n"
                "Если были траты или доходы, внеси их сейчас. Можно просто написать: «кофе 250».\n"
                "Если день без операций — ответь «сегодня ничего», и я спокойно отстану до завтра."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Ручной ввод", callback_data="manual_input")],
                [InlineKeyboardButton(text="🤖 ИИ-помощник", callback_data="ai_assistant")],
            ])
            try:
                await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
                await execute(
                    """INSERT INTO daily_activity_reminders (user_id, reminder_date)
                       VALUES (%s, CURRENT_DATE)
                       ON CONFLICT DO NOTHING""",
                    (user_id,),
                )
            except Exception as e:
                logger.error(f"Failed to send activity reminder to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Daily activity reminder scheduler error: {e}")


async def _weekly_stats(user_id: int, start: date, end: date) -> dict:
    total = await fetchone(
        """SELECT
              COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0),
              COUNT(*)
           FROM transactions
           WHERE user_id=%s AND transaction_date BETWEEN %s AND %s""",
        (user_id, start, end),
    )
    top = await fetchall(
        """SELECT COALESCE(c.name, 'Без категории'), SUM(t.amount) AS total
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s
             AND t.type='expense'
             AND t.transaction_date BETWEEN %s AND %s
           GROUP BY c.name
           ORDER BY total DESC
           LIMIT 5""",
        (user_id, start, end),
    )
    goal = await fetchone(
        "SELECT goal_text FROM user_goals WHERE user_id=%s ORDER BY updated_at DESC, id DESC LIMIT 1",
        (user_id,),
    )
    notes = await fetchall(
        "SELECT text FROM notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    )
    income = float(total[0]) if total else 0.0
    expense = float(total[1]) if total else 0.0
    return {
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "count": int(total[2]) if total else 0,
        "top": top or [],
        "goal": goal[0] if goal else "",
        "notes": [row[0] for row in (notes or [])],
    }


def _weekly_top_lines(stats: dict) -> str:
    if not stats["top"]:
        return "• пока нет расходов по категориям"
    return "\n".join(
        f"• {escape(str(name))} — <b>{_rub(total)}</b>"
        for name, total in stats["top"]
    )


def _weekly_fallback_insight(stats: dict) -> str:
    if stats["goal"]:
        return "Сверь эту неделю с целью: что из расходов реально помогало ей, а что просто съедало ресурс."
    if stats["count"] == 0:
        return "Данных за неделю нет. Можно начать с пары записей в день — этого уже хватит для первого честного вывода."
    return "Цель пока не задана. Самое время описать её в ИИ-помощнике, чтобы следующие выводы были не просто по цифрам, а по твоему курсу."


async def _weekly_ai_insight(stats: dict) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return _weekly_fallback_insight(stats)

    top_text = "; ".join(f"{name}: {_rub(total)}" for name, total in stats["top"]) or "нет"
    notes_text = "; ".join(str(note)[:160] for note in stats["notes"]) or "нет"
    goal_text = stats["goal"] or "нет"
    prompt = (
        "Сделай короткий живой вывод для недельного финансового отчёта в Telegram. "
        "2-3 предложения, без морализаторства. Если есть финансовая цель — явно соотнеси вывод с ней. "
        "Если цели нет — мягко напомни, что её можно задать в ИИ-помощнике. "
        "Если уместно, используй заметки как контекст.\n\n"
        f"Доходы: {_rub(stats['income'])}\n"
        f"Расходы: {_rub(stats['expense'])}\n"
        f"Баланс: {_rub(stats['balance'])}\n"
        f"Операций: {stats['count']}\n"
        f"Топ расходов: {top_text}\n"
        f"Финансовая цель/описание: {goal_text}\n"
        f"Заметки: {notes_text}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "max_tokens": 220,
                    "messages": [
                        {"role": "system", "content": "Ты аккуратный финансовый помощник. Пиши по-русски, коротко и тепло."},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Weekly AI insight error: {e}")
        return _weekly_fallback_insight(stats)


async def send_weekly_reports(bot):
    try:
        start, end = _week_bounds()
        rows = await fetchall(
            """SELECT u.id
               FROM users u
               WHERE COALESCE(u.subscription_tier, 'free') <> 'free'
                 AND (u.premium_until IS NULL OR u.premium_until > NOW())
                 AND NOT EXISTS (
                     SELECT 1 FROM weekly_reports r
                     WHERE r.user_id = u.id AND r.week_start = %s
                 )""",
            (start,),
        )
        for (user_id,) in rows:
            stats = await _weekly_stats(user_id, start, end)
            if stats["count"] == 0 and not stats["goal"] and not stats["notes"]:
                continue

            insight = await _weekly_ai_insight(stats)
            text = (
                "📆 <b>Итоги недели</b>\n"
                f"{start.strftime('%d.%m')} — {end.strftime('%d.%m')}\n\n"
                f"💰 Пришло: <b>{_rub(stats['income'])}</b>\n"
                f"💸 Ушло: <b>{_rub(stats['expense'])}</b>\n"
                f"⚖️ Итог: <b>{_rub(stats['balance'])}</b>\n"
                f"🧾 Операций: <b>{stats['count']}</b>\n\n"
                "🔎 <b>Куда уходило:</b>\n"
                + _weekly_top_lines(stats)
                + "\n\n"
                "🤖 <b>Вывод:</b>\n"
                f"<blockquote>{escape(insight)}</blockquote>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Обсудить с ИИ", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="📊 Отчёты", callback_data="reports_menu")],
            ])
            try:
                await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
                await execute(
                    """INSERT INTO weekly_reports (user_id, week_start)
                       VALUES (%s, %s)
                       ON CONFLICT DO NOTHING""",
                    (user_id, start),
                )
            except Exception as e:
                logger.error(f"Failed to send weekly report to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Weekly report scheduler error: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_reminders, "cron", hour=9, minute=0, args=[bot])
    scheduler.add_job(send_trial_journey_messages, "cron", hour=10, minute=0, args=[bot])
    scheduler.add_job(send_daily_activity_reminders, "cron", hour=20, minute=30, args=[bot])
    scheduler.add_job(send_weekly_reports, "cron", day_of_week="sun", hour=19, minute=0, args=[bot])
    scheduler.start()
    return scheduler
