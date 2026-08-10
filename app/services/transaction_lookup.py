import re

from app.database import fetchall


MONTHS = {
    "январь": 1, "января": 1, "январе": 1,
    "февраль": 2, "февраля": 2, "феврале": 2,
    "март": 3, "марта": 3, "марте": 3,
    "апрель": 4, "апреля": 4, "апреле": 4,
    "май": 5, "мая": 5, "мае": 5,
    "июнь": 6, "июня": 6, "июне": 6,
    "июль": 7, "июля": 7, "июле": 7,
    "август": 8, "августа": 8, "августе": 8,
    "сентябрь": 9, "сентября": 9, "сентябре": 9,
    "октябрь": 10, "октября": 10, "октябре": 10,
    "ноябрь": 11, "ноября": 11, "ноябре": 11,
    "декабрь": 12, "декабря": 12, "декабре": 12,
}


def _month_from_text(text: str | None) -> tuple[int | None, int | None]:
    lower = (text or "").lower().replace("ё", "е")
    month = None
    for name, number in MONTHS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", lower):
            month = number
            break
    if not month:
        return None, None
    year_match = re.search(r"\b(20\d{2})\b", lower)
    return (int(year_match.group(1)) if year_match else None), month


def _word_variants(word: str) -> list[str]:
    variants = [word]
    if len(word) >= 5:
        stem = re.sub(r"[аеёиоуыэюя]+$", "", word)
        if len(stem) >= 4 and stem not in variants:
            variants.append(stem)
    return variants


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
    query = re.sub(
        r"\b(?:" + "|".join(re.escape(name) for name in MONTHS) + r")\b",
        " ",
        query,
    )
    query = re.sub(r"\b20\d{2}\b", " ", query)
    query = " ".join(query.split())
    if len(query) < 3:
        return None
    return query[:80]


async def find_transaction_mentions(user_id: int, user_message: str | None, limit: int = 10) -> tuple[str | None, list]:
    query = extract_history_lookup_query(user_message)
    if not query:
        return None, []

    year, month = _month_from_text(user_message)
    words = [word for word in re.findall(r"[а-яa-z0-9]+", query.lower()) if len(word) >= 3]
    if not words:
        return query, []

    conditions = []
    params: list[object] = [user_id]
    for word in words[:4]:
        variant_conditions = []
        for variant in _word_variants(word):
            variant_conditions.append("(LOWER(COALESCE(t.comment, '')) LIKE %s OR LOWER(COALESCE(c.name, '')) LIKE %s)")
            like = "%" + variant + "%"
            params.extend([like, like])
        conditions.append("(" + " OR ".join(variant_conditions) + ")")

    if month:
        conditions.append("EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.append(month)
    if year:
        conditions.append("EXTRACT(YEAR FROM t.transaction_date) = %s")
        params.append(year)

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
