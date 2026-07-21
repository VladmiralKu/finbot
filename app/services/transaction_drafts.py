import json
import os
import re
from datetime import date, timedelta

import httpx

from app.database import get_categories
from app.services.category_matcher import match_category


AMOUNT_RE = re.compile(r"(?<!\d)([+-]?\d[\d\s]*(?:[,.]\d{1,2})?)(?!\d)")


def _amount(value) -> float | None:
    if value is None:
        return None
    try:
        return abs(float(str(value).replace(" ", "").replace(",", ".").lstrip("+-")))
    except ValueError:
        return None


def _date_value(value) -> date:
    if not value:
        return date.today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return date.today()


def _guess_type(text: str, raw_type: str | None = None) -> str:
    if raw_type in ("expense", "income"):
        return raw_type
    lower = (text or "").lower()
    income_words = (
        "зарплат", "пришло", "поступ", "получил", "получила", "доход",
        "выруч", "заработ", "аванс", "перевели", "кэшбек", "кешбек",
    )
    if any(word in lower for word in income_words):
        return "income"
    if str(text).strip().startswith("+"):
        return "income"
    return "expense"


def _fallback_transactions(text: str) -> list[dict]:
    result = []
    today = date.today()
    normalized = (text or "").replace(";", "\n")
    normalized = re.sub(r"\b(сегодня|вчера|позавчера)\b", r"\n\1", normalized, flags=re.IGNORECASE)
    parts = [part.strip(" .,:;-") for part in re.split(r"\n+", normalized) if part.strip(" .,:;-")]
    for part in parts:
        match = AMOUNT_RE.search(part)
        if not match:
            continue
        amount = _amount(match.group(1))
        if not amount:
            continue
        lower = part.lower()
        tx_date = today
        if "позавчера" in lower:
            tx_date = today - timedelta(days=2)
        elif "вчера" in lower:
            tx_date = today - timedelta(days=1)
        result.append({
            "amount": amount,
            "type": _guess_type(part),
            "transaction_date": tx_date,
            "comment": AMOUNT_RE.sub(" ", part, count=1).strip(" .,:;-"),
        })
    return result


async def _ask_ai_for_transactions(user_id: int, text: str, previous_draft: list[dict] | None = None) -> list[dict]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return []

    categories = await get_categories(user_id)
    categories_payload = [
        {
            "name": cat["name"],
            "type": cat["type"],
            "kind": cat["kind"],
        }
        for cat in categories
    ]
    today = date.today()
    payload = {
        "today": today.isoformat(),
        "user_text": text,
        "previous_draft": previous_draft or [],
        "categories": categories_payload,
    }
    system_prompt = (
        "Ты разбираешь неструктурированный русский текст пользователя в черновик финансовых операций. "
        "Верни JSON строго такого вида: {\"transactions\": [...], \"question\": null}. "
        "Каждая операция: amount, type, transaction_date, category_hint, comment. "
        "type только expense или income. transaction_date строго YYYY-MM-DD. "
        "Разреши относительные даты от today: сегодня, вчера, позавчера, 3 дня назад. "
        "Если в одном сообщении несколько сумм за разные дни или разные покупки, раздели их на отдельные операции. "
        "Не добавляй того, чего пользователь явно не говорил. "
        "Если категорию точно не знаешь, category_hint оставь коротким описанием траты. "
        "comment должен сохранять полезный смысл строки, но без лишней болтовни. "
        "Если передан previous_draft, используй user_text как уточнение к нему и верни полный обновлённый список."
    )

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "response_format": {"type": "json_object"},
                    "max_tokens": 900,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
            )
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
    except Exception:
        return []

    transactions = parsed.get("transactions")
    return transactions if isinstance(transactions, list) else []


async def build_transaction_draft(
    user_id: int,
    text: str,
    previous_draft: list[dict] | None = None,
) -> list[dict]:
    raw_transactions = await _ask_ai_for_transactions(user_id, text, previous_draft)
    if not raw_transactions:
        raw_transactions = _fallback_transactions(text)

    result = []
    for raw in raw_transactions:
        if not isinstance(raw, dict):
            continue
        amount = _amount(raw.get("amount"))
        if not amount:
            continue
        tx_type = _guess_type(
            " ".join(str(raw.get(key) or "") for key in ("comment", "category_hint")),
            raw.get("type"),
        )
        tx_date = _date_value(raw.get("transaction_date"))
        comment = str(raw.get("comment") or raw.get("category_hint") or "").strip()
        category_text = " ".join(
            part for part in (
                str(raw.get("category_hint") or "").strip(),
                comment,
            )
            if part
        )
        category = await match_category(
            user_id,
            category_text or comment or text,
            type_hint=tx_type,
            amount=amount,
            source="ai_action_draft",
        )
        if not category:
            continue
        result.append({
            "amount": amount,
            "type": category["type"],
            "category_id": category["category_id"],
            "category_name": category["category_name"],
            "kind": category["kind"],
            "comment": comment,
            "transaction_date": tx_date,
            "pnl_period": None,
        })

    return result


def serialize_draft(transactions: list[dict]) -> list[dict]:
    result = []
    for tx in transactions:
        item = dict(tx)
        tx_date = item.get("transaction_date")
        if isinstance(tx_date, date):
            item["transaction_date"] = tx_date.isoformat()
        result.append(item)
    return result


def deserialize_draft(transactions: list[dict] | None) -> list[dict]:
    result = []
    for tx in transactions or []:
        item = dict(tx)
        item["transaction_date"] = _date_value(item.get("transaction_date"))
        result.append(item)
    return result
