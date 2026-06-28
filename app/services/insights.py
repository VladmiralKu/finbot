from datetime import date

from app.database import fetchall, fetchone


def _rub(value) -> str:
    return f"{float(value):,.0f}".replace(",", " ") + " ₽"


def _previous_month(year: int, month: int) -> tuple[int, int]:
    month -= 1
    if month <= 0:
        month = 12
        year -= 1
    return year, month


async def build_transaction_insight(user_id: int, tx_id: int) -> str | None:
    """Return one short, calm insight for a saved transaction, or None."""
    tx = await fetchone(
        """SELECT t.id, t.amount, t.type, t.category_id, t.transaction_date, c.name
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.id=%s""",
        (user_id, tx_id),
    )
    if not tx:
        return None

    _, amount, type_, category_id, tx_date, category_name = tx
    if type_ != "expense":
        return None

    amount = float(amount)
    tx_date = tx_date or date.today()
    category_name = category_name or "эта категория"

    same_today = await fetchone(
        """SELECT COUNT(*)
           FROM transactions
           WHERE user_id=%s AND category_id=%s AND type='expense'
             AND transaction_date=%s""",
        (user_id, category_id, tx_date),
    )
    same_today_count = int(same_today[0]) if same_today else 0
    if same_today_count >= 3:
        return f"Кстати, это уже {same_today_count}-я трата в категории «{category_name}» сегодня."

    previous_max = await fetchone(
        """SELECT COUNT(*), COALESCE(MAX(amount), 0)
           FROM transactions
           WHERE user_id=%s AND type='expense' AND id<>%s
             AND EXTRACT(YEAR FROM transaction_date)=%s
             AND EXTRACT(MONTH FROM transaction_date)=%s""",
        (user_id, tx_id, tx_date.year, tx_date.month),
    )
    prev_count = int(previous_max[0]) if previous_max else 0
    prev_max_amount = float(previous_max[1]) if previous_max else 0.0
    if prev_count >= 3 and amount > prev_max_amount and amount >= 1000:
        return "Это самая крупная покупка месяца."

    current_category = await fetchone(
        """SELECT COALESCE(SUM(amount), 0)
           FROM transactions
           WHERE user_id=%s AND category_id=%s AND type='expense'
             AND EXTRACT(YEAR FROM transaction_date)=%s
             AND EXTRACT(MONTH FROM transaction_date)=%s""",
        (user_id, category_id, tx_date.year, tx_date.month),
    )
    current_total = float(current_category[0]) if current_category else 0.0

    prev_year, prev_month = _previous_month(tx_date.year, tx_date.month)
    previous_category = await fetchone(
        """SELECT COALESCE(SUM(amount), 0)
           FROM transactions
           WHERE user_id=%s AND category_id=%s AND type='expense'
             AND EXTRACT(YEAR FROM transaction_date)=%s
             AND EXTRACT(MONTH FROM transaction_date)=%s""",
        (user_id, category_id, prev_year, prev_month),
    )
    previous_total = float(previous_category[0]) if previous_category else 0.0
    if previous_total >= 1000 and current_total >= previous_total * 1.5 and current_total - previous_total >= 1000:
        return f"Расходы на «{category_name}» уже выше прошлого месяца: {_rub(current_total)} против {_rub(previous_total)}."

    leaders = await fetchall(
        """SELECT t.category_id, c.name, SUM(t.amount) AS total
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.type='expense'
             AND EXTRACT(YEAR FROM t.transaction_date)=%s
             AND EXTRACT(MONTH FROM t.transaction_date)=%s
           GROUP BY t.category_id, c.name
           ORDER BY total DESC
           LIMIT 2""",
        (user_id, tx_date.year, tx_date.month),
    )
    if len(leaders) >= 2:
        top_id, top_name, top_total = leaders[0]
        _, _, second_total = leaders[1]
        top_total = float(top_total)
        second_total = float(second_total)
        if top_id == category_id and top_total >= 3000 and top_total >= second_total * 1.2:
            return f"«{top_name or category_name}» сейчас лидер расходов месяца: {_rub(top_total)}."

    similar_large = await fetchone(
        """SELECT COUNT(*)
           FROM transactions
           WHERE user_id=%s AND category_id=%s AND type='expense' AND id<>%s
             AND transaction_date >= %s::date - INTERVAL '45 days'
             AND amount BETWEEN %s AND %s""",
        (user_id, category_id, tx_id, tx_date, amount * 0.9, amount * 1.1),
    )
    if amount >= 5000 and similar_large and int(similar_large[0]) >= 1:
        return f"Похоже на повтор крупной покупки в категории «{category_name}»."

    return None


async def build_first_transaction_insight(user_id: int, tx_ids: list[int]) -> str | None:
    for tx_id in tx_ids:
        insight = await build_transaction_insight(user_id, tx_id)
        if insight:
            return insight
    return None
