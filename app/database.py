import psycopg
import os
from dotenv import load_dotenv

load_dotenv()

_conn = None


async def get_pool():
    global _conn
    if _conn is None or _conn.closed:
        _conn = await psycopg.AsyncConnection.connect(os.getenv("DATABASE_URL"), autocommit=True)
    return _conn


async def close_pool():
    global _conn
    if _conn and not _conn.closed:
        await _conn.close()


async def execute(query, params=None):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(query, params)


async def fetchone(query, params=None):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchone()


async def fetchall(query, params=None):
    conn = await get_pool()
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


async def get_or_create_user(user_id, username, full_name, lang="ru"):
    user = await fetchone("SELECT id FROM users WHERE id = %s", (user_id,))
    if not user:
        await execute(
            "INSERT INTO users (id, username, full_name, language_code) VALUES (%s,%s,%s,%s)",
            (user_id, username, full_name, lang),
        )
        await execute("SELECT create_default_categories(%s)", (user_id,))
    return user_id


async def is_premium(user_id):
    row = await fetchone(
        "SELECT is_premium, premium_until FROM users WHERE id = %s", (user_id,)
    )
    if not row:
        return False
    from datetime import datetime
    if row[0] and row[1] and row[1] > datetime.now():
        return True
    if row[0] and not row[1]:
        return True
    return False


async def get_categories(user_id, type_=None):
    if type_:
        rows = await fetchall(
            "SELECT id, name, type, kind FROM categories WHERE user_id=%s AND type=%s ORDER BY sort_order",
            (user_id, type_),
        )
    else:
        rows = await fetchall(
            "SELECT id, name, type, kind FROM categories WHERE user_id=%s ORDER BY type, sort_order",
            (user_id,),
        )
    return [{"id": r[0], "name": r[1], "type": r[2], "kind": r[3]} for r in rows]


async def add_transaction(user_id, category_id, amount, type_, kind, comment="", receipt_photo_id=None):
    row = await fetchone(
        """INSERT INTO transactions (user_id, category_id, amount, type, kind, comment, receipt_photo_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (user_id, category_id, amount, type_, kind, comment, receipt_photo_id),
    )
    return {"id": row[0]}


async def get_monthly_summary(user_id, year, month):
    # Текущий месяц
    rows = await fetchall(
        """SELECT type, kind, SUM(amount) FROM transactions
           WHERE user_id=%s AND EXTRACT(YEAR FROM transaction_date)=%s AND EXTRACT(MONTH FROM transaction_date)=%s
           GROUP BY type, kind""",
        (user_id, year, month),
    )
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

    # Перетекающий остаток — сумма всех транзакций до начала этого месяца
    prev_row = await fetchone(
        """SELECT 
               COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0)
           FROM transactions
           WHERE user_id=%s AND transaction_date < DATE_TRUNC('month', MAKE_DATE(%s, %s, 1))""",
        (user_id, year, month),
    )
    result["carry_over"] = float(prev_row[0]) if prev_row else 0.0
    result["closing_balance"] = result["carry_over"] + result["balance"]
    return result


async def get_recent_transactions(user_id, limit=10):
    rows = await fetchall(
        """SELECT t.id, t.amount, t.type, t.kind, t.comment, t.transaction_date, c.name
           FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s ORDER BY t.created_at DESC LIMIT %s""",
        (user_id, limit),
    )
    return [{"id": r[0], "amount": r[1], "type": r[2], "kind": r[3], "comment": r[4], "transaction_date": r[5], "category_name": r[6]} for r in rows]


async def get_category_breakdown(user_id, year, month):
    rows = await fetchall(
        """SELECT c.name, c.kind, SUM(t.amount) FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.type='expense'
             AND EXTRACT(YEAR FROM t.transaction_date)=%s
             AND EXTRACT(MONTH FROM t.transaction_date)=%s
           GROUP BY c.name, c.kind ORDER BY 3 DESC""",
        (user_id, year, month),
    )
    return [{"name": r[0], "kind": r[1], "total": r[2]} for r in rows]


async def delete_transaction(tx_id, user_id):
    await execute("DELETE FROM transactions WHERE id=%s AND user_id=%s", (tx_id, user_id))


# --- Recurring payments ---

async def add_recurring_payment(user_id, name, amount, type_, kind, category_id,
                                repeat_type, repeat_day_of_month=None,
                                repeat_day_of_week=None, remind_days_before=1,
                                amount_is_approximate=False):
    from datetime import date, timedelta
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

    await execute(
        """INSERT INTO recurring_payments
           (user_id, name, amount, amount_is_approximate, type, kind, category_id,
            repeat_type, repeat_day_of_month, repeat_day_of_week,
            remind_days_before, next_trigger_date)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (user_id, name, amount, amount_is_approximate, type_, kind, category_id,
         repeat_type, repeat_day_of_month, repeat_day_of_week,
         remind_days_before, next_date)
    )


async def get_recurring_payments(user_id):
    rows = await fetchall(
        """SELECT r.id, r.user_id, r.name, r.amount, r.amount_is_approximate,
                  r.type, r.kind, r.category_id, r.repeat_type,
                  r.repeat_day_of_month, r.repeat_day_of_week,
                  r.remind_days_before, r.is_active, r.last_triggered_at,
                  r.next_trigger_date, c.name as category_name
           FROM recurring_payments r
           LEFT JOIN categories c ON r.category_id = c.id
           WHERE r.user_id = %s AND r.is_active = TRUE
           ORDER BY r.next_trigger_date""",
        (user_id,)
    )
    return rows


async def get_todays_reminders():
    from datetime import date
    today = date.today()
    rows = await fetchall(
        """SELECT r.id, r.user_id, r.name, r.amount, r.amount_is_approximate,
                  r.type, r.kind, r.remind_days_before, r.repeat_type,
                  r.repeat_day_of_month, r.repeat_day_of_week,
                  r.last_triggered_at, r.is_active, r.created_at,
                  r.next_trigger_date, u.id as uid
           FROM recurring_payments r
           JOIN users u ON r.user_id = u.id
           WHERE r.is_active = TRUE
             AND r.next_trigger_date - r.remind_days_before <= %s
             AND r.next_trigger_date >= %s
             AND (r.last_triggered_at IS NULL OR r.last_triggered_at::date < %s)""",
        (today, today, today)
    )
    return rows


async def mark_reminder_sent(payment_id):
    from datetime import datetime, date, timedelta
    row = await fetchone(
        "SELECT repeat_type, repeat_day_of_month, repeat_day_of_week, next_trigger_date FROM recurring_payments WHERE id = %s",
        (payment_id,)
    )
    if row:
        repeat_type, day_month, day_week, current_next = row
        if repeat_type == 'monthly' and day_month:
            if current_next.month == 12:
                new_next = current_next.replace(year=current_next.year+1, month=1, day=day_month)
            else:
                new_next = current_next.replace(month=current_next.month+1, day=day_month)
        elif repeat_type == 'weekly' and day_week is not None:
            new_next = current_next + timedelta(weeks=1)
        else:
            new_next = current_next + timedelta(days=1)
        await execute(
            "UPDATE recurring_payments SET last_triggered_at = %s, next_trigger_date = %s WHERE id = %s",
            (datetime.now(), new_next, payment_id)
        )


# --- Promo codes ---

async def get_promo(code: str):
    return await fetchone(
        "SELECT id, code, tier, days, max_uses, used_count, expires_at FROM promo_codes WHERE code = %s",
        (code.upper(),)
    )


async def use_promo(user_id: int, promo_id: int, tier: str, days: int):
    from datetime import datetime, timedelta
    existing = await fetchone(
        "SELECT id FROM promo_uses WHERE user_id=%s AND promo_id=%s",
        (user_id, promo_id)
    )
    if existing:
        return False
    until = datetime.now() + timedelta(days=days)
    await execute(
        "UPDATE users SET is_premium=TRUE, premium_until=%s WHERE id=%s",
        (until, user_id)
    )
    await execute(
        "UPDATE promo_codes SET used_count=used_count+1 WHERE id=%s",
        (promo_id,)
    )
    await execute(
        "INSERT INTO promo_uses (user_id, promo_id) VALUES (%s,%s)",
        (user_id, promo_id)
    )
    return True


async def get_subscription_tier(user_id: int) -> str:
    row = await fetchone(
        "SELECT is_premium, premium_until FROM users WHERE id=%s",
        (user_id,)
    )
    if not row:
        return 'free'
    from datetime import datetime
    if row[0] and row[1] and row[1] > datetime.now():
        return 'premium'
    if row[0] and not row[1]:
        return 'premium'
    return 'free'


async def activate_stars_payment(user_id: int, tier: str = 'premium', days: int = 30):
    from datetime import datetime, timedelta
    until = datetime.now() + timedelta(days=days)
    await execute(
        "UPDATE users SET is_premium=TRUE, premium_until=%s WHERE id=%s",
        (until, user_id)
    )


async def get_user_tier(user_id):
    row = await fetchone(
        "SELECT subscription_tier, premium_until FROM users WHERE id = %s",
        (user_id,)
    )
    if not row:
        return 'free'
    tier = row[0] or 'free'
    from datetime import datetime
    # Если тариф платный но срок истёк — возвращаем free
    if tier != 'free' and row[1] and row[1] < datetime.now():
        return 'free'
    return tier


async def can_use_feature(user_id, feature):
    """
    Проверяет доступ к фиче по тарифу.
    feature: 'calendar', 'receipt_scan', 'voice_input', 'change_categories',
             'ai_analysis', 'annual_plan', 'pnl_table', 'dds_categories',
             'export', 'business_tools'
    Возвращает: True/False или число (лимит для ai_analysis)
    """
    tier = await get_user_tier(user_id)
    row = await fetchone(
        "SELECT * FROM tier_limits WHERE tier = %s", (tier,)
    )
    if not row:
        return False

    # Маппинг колонок
    cols = ['tier', 'ai_analyses_per_month', 'dashboards_per_month',
            'receipt_scans_per_month', 'excel_imports_per_month',
            'can_change_categories', 'can_change_currency',
            'has_calendar', 'has_pnl', 'has_business_tools',
            'has_voice_input', 'has_annual_plan', 'has_dds_categories', 'has_export']
    data = dict(zip(cols, row))

    feature_map = {
        'calendar': data.get('has_calendar', False),
        'receipt_scan': data.get('receipt_scans_per_month', 0),
        'voice_input': data.get('has_voice_input', False),
        'change_categories': data.get('can_change_categories', False),
        'ai_analysis': data.get('ai_analyses_per_month', 0),
        'annual_plan': data.get('has_annual_plan', False),
        'pnl_table': data.get('has_pnl', False),
        'dds_categories': data.get('has_dds_categories', False),
        'export': data.get('has_export', False),
        'business_tools': data.get('has_business_tools', False),
        'dashboard': data.get('dashboards_per_month', 0),
    }
    return feature_map.get(feature, False)
