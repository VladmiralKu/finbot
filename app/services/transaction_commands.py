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


def _fallback_category_change_parse(text: str) -> dict:
    lower = (text or "").lower()
    parsed = {"tx_id": None, "amount": None, "new_category": None, "month": None, "year": None}

    tx_match = re.search(r"(?:#|транзакци[яюи]\s*|операци[яюи]\s*|номер\s*)(\d+)", lower)
    if tx_match:
        parsed["tx_id"] = int(tx_match.group(1))

    amount_match = re.search(
        r"(?:сумм[ауы]?\s*)?(\d[\d\s]*(?:[,.]\d+)?)\s*(?:р|руб|рубл)",
        lower,
    )
    if amount_match:
        parsed["amount"] = float(amount_match.group(1).replace(" ", "").replace(",", "."))

    if not parsed["tx_id"] and not parsed["amount"]:
        loose_tx_match = re.search(r"(?:^|\s)#?(\d{2,})(?=.*\b(?:на|в)\b)", lower)
        if loose_tx_match:
            parsed["tx_id"] = int(loose_tx_match.group(1))

    period = _month_from_text(lower)
    if period:
        parsed["year"], parsed["month"] = period

    category_match = re.search(r"\b(?:на|в)\s+([а-яёa-z0-9 /-]{2,})$", lower)
    if category_match:
        category_text = category_match.group(1).strip(" .,!?:;-")
        category_text = re.sub(r"\b(?:пожалуйста|плиз|спасибо)\b", "", category_text).strip()
        parsed["new_category"] = category_text or None

    return parsed


def _json_object_from_text(content: str) -> dict:
    cleaned = (content or "").strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


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
    fallback = _fallback_category_change_parse(text)
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
    parsed = _json_object_from_text(content)
    for key, value in fallback.items():
        if parsed.get(key) in (None, "") and value not in (None, ""):
            parsed[key] = value

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


def _fallback_transaction_edit_parse(text: str) -> dict:
    lower = (text or "").lower()
    parsed = {
        "amount": None,
        "new_category": None,
        "comment": None,
        "day": None,
        "month": None,
        "year": None,
    }

    amount_match = re.search(
        r"(?:сумм[ауы]?\s*(?:на)?\s*)?([+-]?\d[\d\s]*(?:[,.]\d+)?)\s*(?:р|руб|рубл)",
        lower,
    )
    if amount_match:
        parsed["amount"] = float(amount_match.group(1).replace(" ", "").replace(",", "."))

    date_match = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](20\d{2}))?\b", lower)
    if date_match:
        parsed["day"] = int(date_match.group(1))
        parsed["month"] = int(date_match.group(2))
        parsed["year"] = int(date_match.group(3)) if date_match.group(3) else date.today().year

    category_match = re.search(
        r"(?:категори[яю]|стать[яю]|раздел)?\s*(?:на|в)\s+([а-яёa-z0-9 /-]{2,})$",
        lower,
    )
    if category_match:
        parsed["new_category"] = category_match.group(1).strip(" .,!?:;-")

    comment_match = re.search(r"(?:комментари[йя]|описание|подпись)\s+(.+)$", text or "", re.IGNORECASE)
    if comment_match:
        parsed["comment"] = comment_match.group(1).strip()

    return parsed


async def parse_transaction_edit(user_id: int, text: str, current: dict) -> dict:
    categories = await get_categories(user_id)
    category_names = [cat["name"] for cat in categories]
    fallback = _fallback_transaction_edit_parse(text)
    prompt = (
        "Разбери просьбу отредактировать УЖЕ ВЫБРАННУЮ финансовую операцию. "
        "Пользователь уже выбрал конкретную операцию, искать другую не нужно. "
        "Верни JSON без пояснений: amount, new_category, comment, day, month, year. "
        "Заполняй только поля, которые пользователь явно просит изменить. "
        "Если поле не меняется, верни null. "
        "new_category выбери как текст из просьбы пользователя. "
        "Доступные категории: " + ", ".join(category_names) + "\n"
        "Текущая операция: " + json.dumps(current, ensure_ascii=False, default=str)
    )
    parsed = {}
    try:
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
        parsed = _json_object_from_text(content)
    except Exception:
        parsed = {}

    for key, value in fallback.items():
        if parsed.get(key) in (None, "") and value not in (None, ""):
            parsed[key] = value

    new_category_text = parsed.get("new_category")
    category = None
    if new_category_text:
        category = await match_category(
            user_id,
            new_category_text,
            type_hint=None,
            amount=parsed.get("amount") or current.get("amount"),
            source="edit_transaction",
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
