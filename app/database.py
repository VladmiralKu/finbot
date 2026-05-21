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
