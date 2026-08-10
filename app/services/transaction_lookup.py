import re

from app.database import fetchall


def extract_history_lookup_query(user_message: str | None) -> str | None:
    lower = (user_message or "").lower().replace("ё", "е")
    if not any(marker in lower for marker in ("когда", "последн", "покуп", "трат", "платил", "оплат")):
        return None

    patterns = (
        r"(?:покупал[аи]?|купил[аи]?|бра[лла]?|платил[аи]? за|оплачивал[аи]?|тратил[аи]? на)\s+(.+)",
        r"(?:последн\w*\s+раз)\s+(.+)",
    )
    query = ""
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            query = match.group(1)
            break
    if not query:
        return None

    query = re.sub(r"[?.!,;:]+", " ", query)
    query = re.sub(
        r"\b(?:я|мы|мне|нам|когда|последний|последняя|последнее|последние|раз|покупал|покупала|купил|купила|за|на|в|и|или|это|было|была|был|были)\b",
        " ",
        query,
    )
    query = " ".join(query.split())
    if len(query) < 3:
        return None
    return query[:80]


async def find_transaction_mentions(user_id: int, user_message: str | None, limit: int = 10) -> tuple[str | None, list]:
    query = extract_history_lookup_query(user_message)
    if not query:
        return None, []

    words = [word for word in re.findall(r"[а-яa-z0-9]+", query.lower()) if len(word) >= 3]
    if not words:
        return query, []

    conditions = []
    params: list[object] = [user_id]
    for word in words[:4]:
        conditions.append("(LOWER(COALESCE(t.comment, '')) LIKE %s OR LOWER(COALESCE(c.name, '')) LIKE %s)")
        like = "%" + word + "%"
        params.extend([like, like])

    rows = await fetchall(
        """SELECT t.id, t.transaction_date, t.amount, t.type, COALESCE(c.name, 'Без категории'), t.comment
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
             AND """ + " AND ".join(conditions) + """
           ORDER BY t.transaction_date DESC, t.created_at DESC, t.id DESC
           LIMIT %s""",
        tuple(params + [int(limit)]),
    )
    return query, rows


def format_transaction_mentions(query: str, rows: list) -> str:
    if not rows:
        return "По всей базе не нашёл операций по запросу «" + query + "»."

    latest = rows[0]
    sign = "-" if latest[3] == "expense" else "+"
    comment = (" | " + latest[5]) if latest[5] else ""
    text = (
        "Последний раз по запросу «" + query + "»:\n"
        + latest[1].strftime("%d.%m.%Y")
        + " "
        + sign
        + "{:,.0f}".format(abs(float(latest[2])))
        + " ₽ — "
        + str(latest[4] or "Без категории")
        + comment
    )

    if len(rows) > 1:
        lines = []
        for row in rows[1:5]:
            row_sign = "-" if row[3] == "expense" else "+"
            row_comment = (" | " + row[5]) if row[5] else ""
            lines.append(
                row[1].strftime("%d.%m.%Y")
                + " "
                + row_sign
                + "{:,.0f}".format(abs(float(row[2])))
                + " ₽ — "
                + str(row[4] or "Без категории")
                + row_comment
            )
        text += "\n\nЕщё совпадения:\n" + "\n".join(lines)

    return text
