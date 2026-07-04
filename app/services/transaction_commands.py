import json
import os
import re
from datetime import date

import httpx

from app.database import fetchall, fetchone, get_categories
from app.services.category_matcher import match_category
from app.services.transaction_ai import extract_transactions_from_text


MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def _month_from_text(text: str) -> tuple[int, int] | None:
    lower = (text or "").lower()
    year_match = re.search(r"\b(20\d{2})\b", lower)
    year = int(year_match.group(1)) if year_match else date.today().year
    for prefix, month in MONTHS.items():
        if prefix in lower:
            return year, month
    return None


def _tx_tuple(row):
    return (row[0], row[1], row[2], row[3], row[4], row[5])


async def find_transaction_from_text(user_id: int, text: str):
    txs = await extract_transactions_from_text(user_id, text, source="command")
    parsed = txs[0] if txs else {}
    amount = parsed.get("amount")
    category_id = parsed.get("category_id")
    period = _month_from_text(text)

    conditions = ["t.user_id=%s"]
    params = [user_id]
    if amount:
        conditions.append("ABS(t.amount - %s) <= GREATEST(%s * 0.08, 5)")
        params.extend([float(amount), float(amount)])
    if category_id:
        conditions.append("t.category_id=%s")
        params.append(category_id)
    if period:
        year, month = period
        conditions.append("EXTRACT(YEAR FROM t.transaction_date)=%s")
        conditions.append("EXTRACT(MONTH FROM t.transaction_date)=%s")
        params.extend([year, month])

    rows = await fetchall(
        """SELECT t.id, t.transaction_date, t.amount, t.type, t.comment, c.name
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE """ + " AND ".join(conditions) + """
           ORDER BY t.transaction_date DESC, t.created_at DESC
           LIMIT 5""",
        tuple(params),
    )
    if len(rows) == 1:
        return _tx_tuple(rows[0]), []
    if len(rows) > 1:
        return None, [_tx_tuple(row) for row in rows]

    if amount:
        rows = await fetchall(
            """SELECT t.id, t.transaction_date, t.amount, t.type, t.comment, c.name
               FROM transactions t
               LEFT JOIN categories c ON t.category_id = c.id
               WHERE t.user_id=%s AND ABS(t.amount - %s) <= GREATEST(%s * 0.08, 5)
               ORDER BY t.transaction_date DESC, t.created_at DESC
               LIMIT 5""",
            (user_id, float(amount), float(amount)),
        )
        if len(rows) == 1:
            return _tx_tuple(rows[0]), []
        if len(rows) > 1:
            return None, [_tx_tuple(row) for row in rows]

    return None, []


async def parse_category_change(user_id: int, text: str) -> dict:
    categories = await get_categories(user_id)
    category_names = [cat["name"] for cat in categories]
    prompt = (
        "Разбери просьбу изменить категорию у уже записанной транзакции. "
        "Верни JSON без пояснений: tx_id, amount, new_category, month, year. "
        "Если значения нет, верни null. "
        "new_category выбери как текст из просьбы пользователя. "
        "Доступные категории: " + ", ".join(category_names)
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 180,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
            },
            timeout=15.0,
        )
    content = response.json()["choices"][0]["message"]["content"].strip()
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}

    new_category_text = parsed.get("new_category") or text
    category = await match_category(
        user_id,
        new_category_text,
        type_hint=None,
        amount=parsed.get("amount"),
        source="change_category",
    )
    parsed["new_category_id"] = category["category_id"] if category else None
    parsed["new_category_name"] = category["category_name"] if category else None
    return parsed


async def find_transaction_for_category_change(user_id: int, text: str, parsed: dict):
    tx_id = parsed.get("tx_id")
    if tx_id:
        row = await fetchone(
            """SELECT t.id, t.transaction_date, t.amount, t.type, t.comment, c.name
               FROM transactions t
               LEFT JOIN categories c ON t.category_id = c.id
               WHERE t.user_id=%s AND t.id=%s""",
            (user_id, int(tx_id)),
        )
        return _tx_tuple(row) if row else None, []

    amount = parsed.get("amount")
    year = parsed.get("year")
    month = parsed.get("month")
    if not month:
        period = _month_from_text(text)
        if period:
            year, month = period

    conditions = ["t.user_id=%s"]
    params = [user_id]
    if amount:
        conditions.append("ABS(t.amount - %s) <= GREATEST(%s * 0.08, 5)")
        params.extend([float(amount), float(amount)])
    if year and month:
        conditions.append("EXTRACT(YEAR FROM t.transaction_date)=%s")
        conditions.append("EXTRACT(MONTH FROM t.transaction_date)=%s")
        params.extend([int(year), int(month)])

    rows = await fetchall(
        """SELECT t.id, t.transaction_date, t.amount, t.type, t.comment, c.name
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE """ + " AND ".join(conditions) + """
           ORDER BY t.transaction_date DESC, t.created_at DESC
           LIMIT 5""",
        tuple(params),
    )
    if len(rows) == 1:
        return _tx_tuple(rows[0]), []
    return None, [_tx_tuple(row) for row in rows]
