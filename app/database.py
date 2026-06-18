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
        from datetime import datetime, timedelta
        trial_until = datetime.now() + timedelta(days=3)
        await execute(
            """INSERT INTO users (id, username, full_name, language_code, subscription_tier, premium_until)
               VALUES (%s,%s,%s,%s,'premium',%s)""",
            (user_id, username, full_name, lang, trial_until),
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
    amount = abs(amount)
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
        "UPDATE users SET subscription_tier=%s, is_premium=%s, premium_until=%s WHERE id=%s",
        (tier, tier == 'premium', until, user_id)
    )


async def check_and_expire_trial(user_id):
    """Сбрасывает тариф если пробный период истёк"""
    from datetime import datetime
    row = await fetchone(
        "SELECT subscription_tier, premium_until FROM users WHERE id = %s",
        (user_id,)
    )
    if not row:
        return
    tier, until = row
    if tier == 'premium' and until and until < datetime.now():
        await execute(
            "UPDATE users SET subscription_tier = 'free', premium_until = NULL WHERE id = %s",
            (user_id,)
        )


async def get_user_tier(user_id):
    await check_and_expire_trial(user_id)
    row = await fetchone(
        "SELECT subscription_tier, premium_until FROM users WHERE id = %s",
        (user_id,)
    )
    if not row:
        return 'free'
    tier = row[0] or 'free'
    from datetime import datetime
    if tier != 'free' and row[1] and row[1] < datetime.now():
        await execute(
            "UPDATE users SET subscription_tier = 'free' WHERE id = %s",
            (user_id,)
        )
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


async def get_dashboard(user_id, year, month):
    """Данные для табло управленца"""
    from datetime import date
    import calendar

    # Текущий месяц - транзакции по pnl_period
    period = f"{year}-{month:02d}"

    # Доходы и расходы по pnl_period
    rows = await fetchall(
        """SELECT t.type, c.kind, c.name, SUM(t.amount) as total
           FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
             AND COALESCE(t.pnl_period, TO_CHAR(t.transaction_date, 'YYYY-MM')) = %s
           GROUP BY t.type, c.kind, c.name
           ORDER BY t.type, c.kind, c.name""",
        (user_id, period)
    )

    # Прошлый месяц
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_period = f"{prev_year}-{prev_month:02d}"

    prev_rows = await fetchall(
        """SELECT t.type, SUM(t.amount) as total
           FROM transactions t
           WHERE t.user_id = %s
             AND COALESCE(t.pnl_period, TO_CHAR(t.transaction_date, 'YYYY-MM')) = %s
           GROUP BY t.type""",
        (user_id, prev_period)
    )

    # Количество транзакций
    tx_count = await fetchone(
        """SELECT COUNT(*) FROM transactions
           WHERE user_id = %s
             AND EXTRACT(YEAR FROM transaction_date) = %s
             AND EXTRACT(MONTH FROM transaction_date) = %s""",
        (user_id, year, month)
    )

    # Ближайшие платежи из календаря
    upcoming = await fetchall(
        """SELECT name, amount, next_trigger_date
           FROM recurring_payments
           WHERE user_id = %s AND is_active = TRUE
             AND next_trigger_date <= CURRENT_DATE + INTERVAL '7 days'
             AND next_trigger_date >= CURRENT_DATE
           ORDER BY next_trigger_date""",
        (user_id,)
    )

    # Собираем результат
    result = {
        'period': period,
        'income': 0.0,
        'income_by_wallet': {},
        'variable_expense': 0.0,
        'fixed_expense': 0.0,
        'depreciation': 0.0,
        'tax': 0.0,
        'loan_body': 0.0,
        'loan_pct': 0.0,
        'categories': [],
        'prev_income': 0.0,
        'prev_expense': 0.0,
        'tx_count': int(tx_count[0]) if tx_count else 0,
        'upcoming': upcoming or [],
    }

    for row in rows:
        type_, kind, name, total = row
        total = float(total)
        if type_ == 'income':
            result['income'] += total
        elif type_ == 'expense':
            if kind == 'fixed':
                result['fixed_expense'] += total
            elif kind == 'variable':
                result['variable_expense'] += total
            elif kind == 'depreciation':
                result['depreciation'] += total
            elif kind == 'tax':
                result['tax'] += total
            elif kind == 'loan_body':
                result['loan_body'] += total
            elif kind == 'loan_pct':
                result['loan_pct'] += total
            result['categories'].append((name, kind, total))

    for row in prev_rows:
        type_, total = row
        if type_ == 'income':
            result['prev_income'] = float(total)
        else:
            result['prev_expense'] = float(total)

    # Считаем EBITDA и ЧП
    total_expense = result['fixed_expense'] + result['variable_expense']
    result['total_expense'] = total_expense
    result['ebitda'] = result['income'] - total_expense
    result['net_profit'] = result['ebitda'] - result['depreciation'] - result['tax'] - result['loan_body'] - result['loan_pct']
    result['net_profit_pct'] = (result['net_profit'] / result['income'] * 100) if result['income'] > 0 else 0

    # Динамика
    prev_net = result['prev_income'] - result['prev_expense']
    if prev_net != 0:
        result['dynamics'] = ((result['net_profit'] - prev_net) / abs(prev_net) * 100)
    else:
        result['dynamics'] = None

    return result


async def get_transactions_by_month(user_id, year, month):
    rows = await fetchall(
        """SELECT t.id, t.transaction_date, t.amount, t.type, t.comment,
                  c.name as category_name, t.wallet
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
             AND EXTRACT(YEAR FROM t.transaction_date) = %s
             AND EXTRACT(MONTH FROM t.transaction_date) = %s
           ORDER BY t.transaction_date DESC, t.id DESC""",
        (user_id, year, month)
    )
    return rows


async def get_all_transactions_for_export(user_id):
    rows = await fetchall(
        """SELECT t.id, t.transaction_date, t.amount, t.type,
                  c.name as category_name, t.wallet, t.comment, t.pnl_period
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
           ORDER BY t.transaction_date DESC, t.id DESC""",
        (user_id,)
    )
    return rows


async def delete_transaction_by_id(user_id, tx_id):
    row = await fetchone(
        "SELECT id FROM transactions WHERE id=%s AND user_id=%s",
        (tx_id, user_id)
    )
    if not row:
        return False
    await execute(
        "DELETE FROM transactions WHERE id=%s AND user_id=%s",
        (tx_id, user_id)
    )
    return True


async def get_pnl_report(user_id, year, month):
    """ПнЛ отчёт по методу начисления (pnl_period)"""
    period = f"{year}-{month:02d}"

    rows = await fetchall(
        """SELECT t.type, c.kind, c.name, SUM(t.amount) as total
           FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
             AND COALESCE(t.pnl_period, TO_CHAR(t.transaction_date, 'YYYY-MM')) = %s
           GROUP BY t.type, c.kind, c.name
           ORDER BY t.type, c.kind, SUM(t.amount) DESC""",
        (user_id, period)
    )

    result = {
        'period': period,
        'income': 0.0,
        'income_cats': [],
        'variable': 0.0,
        'variable_cats': [],
        'fixed': 0.0,
        'fixed_cats': [],
        'depreciation': 0.0,
        'tax': 0.0,
        'loan_body': 0.0,
        'loan_pct': 0.0,
    }

    for row in rows:
        type_, kind, name, total = row
        total = float(total)
        if type_ == 'income':
            result['income'] += total
            result['income_cats'].append((name, total))
        elif type_ == 'expense':
            if kind == 'variable':
                result['variable'] += total
                result['variable_cats'].append((name, total))
            elif kind == 'fixed':
                result['fixed'] += total
                result['fixed_cats'].append((name, total))
            elif kind == 'depreciation':
                result['depreciation'] += total
            elif kind == 'tax':
                result['tax'] += total
            elif kind == 'loan_body':
                result['loan_body'] += total
            elif kind == 'loan_pct':
                result['loan_pct'] += total

    result['gross_profit'] = result['income'] - result['variable']
    result['ebitda'] = result['gross_profit'] - result['fixed']
    result['net_profit'] = result['ebitda'] - result['depreciation'] - result['tax'] - result['loan_body'] - result['loan_pct']

    def pct(val):
        return f"{val/result['income']*100:.1f}%" if result['income'] > 0 else "—"

    result['pct'] = pct
    return result


async def get_ai_history(user_id: int, limit: int = 20) -> list:
    try:
        rows = await fetchall(
            "SELECT role, content FROM ai_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit)
        )
        return list(reversed([{"role": r["role"], "content": r["content"]} for r in rows]))
    except Exception:
        return []


async def save_ai_message(user_id: int, role: str, content: str):
    try:
        await execute(
            "INSERT INTO ai_history (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
    except Exception:
        pass


async def clear_ai_history(user_id: int):
    try:
        await execute("DELETE FROM ai_history WHERE user_id = %s", (user_id,))
    except Exception:
        pass
