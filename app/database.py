import psycopg
import os
from dotenv import load_dotenv

load_dotenv()

_conn = None


async def get_pool():
    global _conn
    if _conn is None or _conn.closed:
        _conn = await psycopg.AsyncConnection.connect(os.getenv("DATABASE_URL"))
    return _conn


async def close_pool():
    global _conn
    if _conn and not _conn.closed:
        await _conn.close()


async def get_or_create_user(user_id, username, full_name, lang="ru"):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        user = await cur.fetchone()
        if not user:
            await cur.execute(
                "INSERT INTO users (id, username, full_name, language_code) VALUES (%s,%s,%s,%s)",
                (user_id, username, full_name, lang),
            )
            await cur.execute("SELECT create_default_categories(%s)", (user_id,))
            await conn.commit()
    return user_id


async def is_premium(user_id):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute("SELECT is_premium, premium_until FROM users WHERE id = %s", (user_id,))
        row = await cur.fetchone()
    if not row:
        return False
    from datetime import datetime
    if row[0] and row[1] and row[1] > datetime.now():
        return True
    if row[0] and not row[1]:
        return True
    return False


async def get_categories(user_id, type_=None):
    conn = await get_pool()
    async with conn.cursor() as cur:
        if type_:
            await cur.execute(
                "SELECT id, name, type, kind FROM categories WHERE user_id=%s AND type=%s ORDER BY sort_order",
                (user_id, type_),
            )
        else:
            await cur.execute(
                "SELECT id, name, type, kind FROM categories WHERE user_id=%s ORDER BY type, sort_order",
                (user_id,),
            )
        rows = await cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "kind": r[3]} for r in rows]


async def add_transaction(user_id, category_id, amount, type_, kind, comment="", receipt_photo_id=None):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO transactions (user_id, category_id, amount, type, kind, comment, receipt_photo_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user_id, category_id, amount, type_, kind, comment, receipt_photo_id),
        )
        row = await cur.fetchone()
        await conn.commit()
    return {"id": row[0]}


async def get_monthly_summary(user_id, year, month):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT type, kind, SUM(amount) FROM transactions
               WHERE user_id=%s AND EXTRACT(YEAR FROM transaction_date)=%s AND EXTRACT(MONTH FROM transaction_date)=%s
               GROUP BY type, kind""",
            (user_id, year, month),
        )
        rows = await cur.fetchall()
    result = {"income": 0.0, "expense_fixed": 0.0, "expense_variable": 0.0}
    for row in rows:
        if row[0] == "income":
            result["income"] += float(row[2])
        elif row[0] == "expense" and row[1] == "fixed":
            result["expense_fixed"] += float(row[2])
        elif row[0] == "expense" and row[1] == "variable":
            result["expense_variable"] += float(row[2])
    result["total_expense"] = result["expense_fixed"] + result["expense_variable"]
    result["balance"] = result["income"] - result["total_expense"]
    return result


async def get_recent_transactions(user_id, limit=10):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT t.id, t.amount, t.type, t.kind, t.comment, t.transaction_date, c.name as category_name
               FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
               WHERE t.user_id=%s ORDER BY t.created_at DESC LIMIT %s""",
            (user_id, limit),
        )
        rows = await cur.fetchall()
    return [{"id": r[0], "amount": r[1], "type": r[2], "kind": r[3], "comment": r[4], "transaction_date": r[5], "category_name": r[6]} for r in rows]


async def get_category_breakdown(user_id, year, month):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT c.name, c.kind, SUM(t.amount) FROM transactions t
               JOIN categories c ON t.category_id = c.id
               WHERE t.user_id=%s AND t.type='expense'
                 AND EXTRACT(YEAR FROM t.transaction_date)=%s
                 AND EXTRACT(MONTH FROM t.transaction_date)=%s
               GROUP BY c.name, c.kind ORDER BY 3 DESC""",
            (user_id, year, month),
        )
        rows = await cur.fetchall()
    return [{"name": r[0], "kind": r[1], "total": r[2]} for r in rows]


async def delete_transaction(tx_id, user_id):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute("DELETE FROM transactions WHERE id=%s AND user_id=%s", (tx_id, user_id))
        await conn.commit()


# --- Recurring payments ---

async def add_recurring_payment(user_id, name, amount, type_, kind, category_id,
                                  repeat_type, repeat_day_of_month=None,
                                  repeat_day_of_week=None, remind_days_before=1,
                                  amount_is_approximate=False):
    from datetime import date, timedelta
    pool = await get_pool()
    # Считаем следующую дату
    today = date.today()
    if repeat_type == 'monthly' and repeat_day_of_month:
        if today.day <= repeat_day_of_month:
            next_date = today.replace(day=repeat_day_of_month)
        else:
            if today.month == 12:
                next_date = today.replace(year=today.year+1, month=1, day=repeat_day_of_month)
            else:
                next_date = today.replace(month=today.month+1, day=repeat_day_of_month)
    elif repeat_type == 'weekly' and repeat_day_of_week is not None:
        days_ahead = repeat_day_of_week - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_date = today + timedelta(days=days_ahead)
    else:
        next_date = today + timedelta(days=1)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO recurring_payments
               (user_id, name, amount, amount_is_approximate, type, kind, category_id,
                repeat_type, repeat_day_of_month, repeat_day_of_week,
                remind_days_before, next_trigger_date)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, name, amount, amount_is_approximate, type_, kind, category_id,
             repeat_type, repeat_day_of_month, repeat_day_of_week,
             remind_days_before, next_date)
        )
        await conn.commit()


async def get_recurring_payments(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT r.*, c.name as category_name
                   FROM recurring_payments r
                   LEFT JOIN categories c ON r.category_id = c.id
                   WHERE r.user_id = %s AND r.is_active = TRUE
                   ORDER BY r.next_trigger_date""",
                (user_id,)
            )
            rows = await cur.fetchall()
    return rows


async def get_todays_reminders():
    """Возвращает все платежи которые нужно напомнить сегодня."""
    from datetime import date, timedelta
    pool = await get_pool()
    today = date.today()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT r.*, u.id as uid
                   FROM recurring_payments r
                   JOIN users u ON r.user_id = u.id
                   WHERE r.is_active = TRUE
                     AND r.next_trigger_date - r.remind_days_before <= %s
                     AND r.next_trigger_date >= %s
                     AND (r.last_triggered_at IS NULL
                          OR r.last_triggered_at::date < %s)""",
                (today, today, today)
            )
            return await cur.fetchall()


async def mark_reminder_sent(payment_id):
    from datetime import datetime, date, timedelta
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT repeat_type, repeat_day_of_month, repeat_day_of_week, next_trigger_date FROM recurring_payments WHERE id = %s",
                (payment_id,)
            )
            row = await cur.fetchone()
        if row:
            repeat_type, day_month, day_week, current_next = row
            today = date.today()
            if repeat_type == 'monthly' and day_month:
                if today.month == 12:
                    new_next = today.replace(year=today.year+1, month=1, day=day_month)
                else:
                    new_next = today.replace(month=today.month+1, day=day_month)
            elif repeat_type == 'weekly' and day_week is not None:
                new_next = current_next + timedelta(weeks=1)
            else:
                new_next = current_next + timedelta(days=1)
            await conn.execute(
                "UPDATE recurring_payments SET last_triggered_at = %s, next_trigger_date = %s WHERE id = %s",
                (datetime.now(), new_next, payment_id)
            )
            await conn.commit()
