import json
import os
import re
from datetime import date

import httpx

from app.database import execute, fetchall, fetchone


MONEY_RE = r"(?<!\d)(\d[\d\s]*(?:[,.]\d{1,2})?)(?!\d)"


def rub(value) -> str:
    return f"{float(value or 0):,.0f}".replace(",", " ") + " ₽"


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return abs(float(value.replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def _amount_after(text: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern + r"[^0-9]{0,35}" + MONEY_RE, text, re.IGNORECASE)
        if match:
            return _money(match.group(match.lastindex))
    return None


def _first_amount(text: str) -> float | None:
    match = re.search(MONEY_RE, text or "")
    return _money(match.group(1)) if match else None


def _payment_day(text: str) -> int | None:
    patterns = (
        r"(?:плат[её]ж|оплат[аы]|вносить|внести|дата)[^0-9]{0,25}(\d{1,2})(?:\s*(?:числ[ао]?|го|ого))?",
        r"(\d{1,2})\s*(?:числ[ао]?|го|ого)",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if not match:
            continue
        day = int(match.group(1))
        if 1 <= day <= 31:
            return day
    return None


def _interest_rate(text: str) -> float | None:
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*%", text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _card_name(text: str) -> str:
    first_part = re.split(r"[,;\n]", text or "", maxsplit=1)[0]
    cleaned = re.sub(MONEY_RE, " ", first_part)
    cleaned = re.sub(
        r"\b(долг|тело|остаток|задолженность|лимит|карта|кредитка|кредитная|платеж|платёж|минимальный|минималка)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    if 2 <= len(cleaned) <= 64:
        return cleaned[:64]

    banks = (
        "тинькофф", "т-банк", "сбер", "альфа", "втб", "озон", "мтс",
        "райффайзен", "газпром", "совком", "почта", "уралсиб",
    )
    lower = (text or "").lower()
    for bank in banks:
        if bank in lower:
            return bank.title()
    return "Кредитка"


def _fallback_card_payload(text: str) -> dict:
    debt = _amount_after(text, ("долг", "тело", "остаток", "задолженность"))
    limit = _amount_after(text, ("лимит",))
    min_payment = _amount_after(text, ("минимальн", "минималк", "мин\\.?\\s*плат"))

    if debt is None:
        debt = _first_amount(text)

    return {
        "name": _card_name(text),
        "debt_amount": debt,
        "credit_limit": limit,
        "min_payment": min_payment,
        "payment_day": _payment_day(text),
        "interest_rate": _interest_rate(text),
    }


async def parse_credit_card_payload(text: str) -> dict:
    fallback = _fallback_card_payload(text)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return fallback

    prompt = (
        "Разбери описание кредитной карты для финансового бота. "
        "Верни JSON с ключами: name, debt_amount, credit_limit, min_payment, payment_day, interest_rate. "
        "debt_amount - текущий долг/тело/остаток долга по карте. "
        "credit_limit - лимит карты. min_payment - минимальный платеж. "
        "payment_day - день месяца платежа, 1-31. interest_rate - годовая ставка в процентах. "
        "Если значения нет, верни null. Суммы верни числами в рублях."
    )
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "response_format": {"type": "json_object"},
                    "max_tokens": 220,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": text},
                    ],
                },
            )
        data = response.json()
        parsed = json.loads(data["choices"][0]["message"]["content"])
    except Exception:
        return fallback

    result = fallback.copy()
    for key in ("name", "debt_amount", "credit_limit", "min_payment", "payment_day", "interest_rate"):
        value = parsed.get(key)
        if value not in (None, "", "null"):
            result[key] = value

    for key in ("debt_amount", "credit_limit", "min_payment", "interest_rate"):
        if result.get(key) is not None:
            try:
                result[key] = float(result[key])
            except (TypeError, ValueError):
                result[key] = fallback.get(key)
    if result.get("payment_day") is not None:
        try:
            day = int(result["payment_day"])
            result["payment_day"] = day if 1 <= day <= 31 else None
        except (TypeError, ValueError):
            result["payment_day"] = fallback.get("payment_day")
    result["name"] = str(result.get("name") or fallback["name"])[:64]
    return result


def parse_topup_amount(text: str) -> float | None:
    return _amount_after(text, ("пополнил", "пополнение", "внес", "внёс", "закинул", "кинул", "погасил", "оплатил")) or _first_amount(text)


def parse_balance_payload(text: str) -> dict:
    debt = _amount_after(text, ("долг", "тело", "остаток", "задолженность"))
    if debt is None:
        debt = _first_amount(text)
    return {
        "debt_amount": debt,
        "credit_limit": _amount_after(text, ("лимит",)),
    }


def parse_limit_amount(text: str) -> float | None:
    return _amount_after(text, ("лимит", "подняли", "увеличили", "стал", "стало", "до")) or _first_amount(text)


def parse_credit_card_edit_payload(text: str) -> dict:
    result = {}

    name_match = re.search(
        r"(?:название|имя|переименуй|переименовать|назови)\s+(?:в\s+)?([^,;\n]+)",
        text or "",
        re.IGNORECASE,
    )
    if name_match:
        name = name_match.group(1).strip(" .:-")
        if name:
            result["name"] = name[:64]

    debt = _amount_after(text, ("долг", "тело", "остаток", "задолженность"))
    if debt is not None:
        result["debt_amount"] = debt

    limit = _amount_after(text, ("лимит",))
    if limit is not None:
        result["credit_limit"] = limit

    min_payment = _amount_after(text, ("минимальн", "минималк", "мин\\.?\\s*плат"))
    if min_payment is not None:
        result["min_payment"] = min_payment

    payment_day = _payment_day(text)
    if payment_day is not None:
        result["payment_day"] = payment_day

    interest_rate = _interest_rate(text)
    if interest_rate is not None:
        result["interest_rate"] = interest_rate

    return result


async def list_credit_cards(user_id: int):
    rows = await fetchall(
        """SELECT id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at
           FROM credit_cards
           WHERE user_id=%s AND is_active=TRUE
           ORDER BY created_at, id""",
        (user_id,),
    )
    return rows or []


async def get_credit_card(user_id: int, card_id: int):
    return await fetchone(
        """SELECT id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at
           FROM credit_cards
           WHERE user_id=%s AND id=%s AND is_active=TRUE""",
        (user_id, card_id),
    )


async def create_credit_card(user_id: int, payload: dict):
    row = await fetchone(
        """INSERT INTO credit_cards
              (user_id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
        (
            user_id,
            payload.get("name") or "Кредитка",
            payload.get("debt_amount") or 0,
            payload.get("credit_limit"),
            payload.get("min_payment"),
            payload.get("payment_day"),
            payload.get("interest_rate"),
        ),
    )
    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, debt_amount, credit_limit, comment)
           VALUES (%s, %s, 'setup', %s, %s, %s)""",
        (user_id, row[0], row[2], row[3], "Создание кредитки"),
    )
    return row


async def add_credit_topup(user_id: int, card_id: int, amount: float, comment: str = ""):
    row = await fetchone(
        """UPDATE credit_cards
           SET debt_amount = GREATEST(COALESCE(debt_amount, 0) - %s, 0),
               updated_at = NOW()
           WHERE user_id=%s AND id=%s AND is_active=TRUE
           RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
        (amount, user_id, card_id),
    )
    if not row:
        return None
    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, amount, debt_amount, credit_limit, comment)
           VALUES (%s, %s, 'topup', %s, %s, %s, %s)""",
        (user_id, card_id, amount, row[2], row[3], comment),
    )
    return row


async def update_credit_balance(user_id: int, card_id: int, debt_amount: float, credit_limit: float | None, comment: str = ""):
    if credit_limit is None:
        row = await fetchone(
            """UPDATE credit_cards
               SET debt_amount=%s, updated_at=NOW()
               WHERE user_id=%s AND id=%s AND is_active=TRUE
               RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
            (debt_amount, user_id, card_id),
        )
    else:
        row = await fetchone(
            """UPDATE credit_cards
               SET debt_amount=%s, credit_limit=%s, updated_at=NOW()
               WHERE user_id=%s AND id=%s AND is_active=TRUE
               RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
            (debt_amount, credit_limit, user_id, card_id),
        )
    if not row:
        return None
    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, debt_amount, credit_limit, comment)
           VALUES (%s, %s, 'balance_update', %s, %s, %s)""",
        (user_id, card_id, row[2], row[3], comment),
    )
    return row


async def update_credit_limit(user_id: int, card_id: int, credit_limit: float, comment: str = ""):
    row = await fetchone(
        """UPDATE credit_cards
           SET credit_limit=%s, updated_at=NOW()
           WHERE user_id=%s AND id=%s AND is_active=TRUE
           RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
        (credit_limit, user_id, card_id),
    )
    if not row:
        return None
    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, debt_amount, credit_limit, comment)
           VALUES (%s, %s, 'limit_update', %s, %s, %s)""",
        (user_id, card_id, row[2], row[3], comment),
    )
    return row


async def update_credit_card_details(user_id: int, card_id: int, updates: dict, comment: str = ""):
    allowed = {
        "name",
        "debt_amount",
        "credit_limit",
        "min_payment",
        "payment_day",
        "interest_rate",
    }
    clean_updates = {key: value for key, value in updates.items() if key in allowed}
    if not clean_updates:
        return None

    set_parts = []
    params = []
    for key, value in clean_updates.items():
        set_parts.append(key + "=%s")
        params.append(value)
    set_parts.append("updated_at=NOW()")
    params.extend([user_id, card_id])

    row = await fetchone(
        """UPDATE credit_cards
           SET """ + ", ".join(set_parts) + """
           WHERE user_id=%s AND id=%s AND is_active=TRUE
           RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
        tuple(params),
    )
    if not row:
        return None

    event_type = "settings_update"
    if "debt_amount" in clean_updates:
        event_type = "balance_update"
    elif "credit_limit" in clean_updates:
        event_type = "limit_update"

    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, debt_amount, credit_limit, comment)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, card_id, event_type, row[2], row[3], comment),
    )
    return row


async def update_last_credit_topup(user_id: int, card_id: int, amount: float, comment: str = ""):
    last = await fetchone(
        """SELECT id, event_type, amount
           FROM credit_card_events
           WHERE user_id=%s AND card_id=%s AND event_type <> 'delete'
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (user_id, card_id),
    )
    if not last:
        return None
    if last[1] != "topup":
        return {"error": "not_latest"}

    event_id = last[0]
    old_amount = float(last[2] or 0)
    delta = float(amount) - old_amount
    row = await fetchone(
        """UPDATE credit_cards
           SET debt_amount = GREATEST(COALESCE(debt_amount, 0) - %s, 0),
               updated_at = NOW()
           WHERE user_id=%s AND id=%s AND is_active=TRUE
           RETURNING id, name, debt_amount, credit_limit, min_payment, payment_day, interest_rate, updated_at""",
        (delta, user_id, card_id),
    )
    if not row:
        return None

    await execute(
        """UPDATE credit_card_events
           SET amount=%s,
               debt_amount=%s,
               credit_limit=%s,
               comment=%s
           WHERE id=%s AND user_id=%s AND card_id=%s""",
        (amount, row[2], row[3], comment, event_id, user_id, card_id),
    )
    return {
        "card": row,
        "old_amount": old_amount,
        "new_amount": amount,
    }


async def delete_credit_card(user_id: int, card_id: int):
    row = await fetchone(
        """UPDATE credit_cards
           SET is_active=FALSE, updated_at=NOW()
           WHERE user_id=%s AND id=%s AND is_active=TRUE
           RETURNING id, name, debt_amount, credit_limit""",
        (user_id, card_id),
    )
    if not row:
        return None
    await execute(
        """INSERT INTO credit_card_events
              (user_id, card_id, event_type, debt_amount, credit_limit, comment)
           VALUES (%s, %s, 'delete', %s, %s, %s)""",
        (user_id, card_id, row[2], row[3], "Кредитка удалена"),
    )
    return row


async def credit_month_summary(user_id: int, card_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    month_start = today.replace(day=1)

    card = await get_credit_card(user_id, card_id)
    if not card:
        return {}

    start_row = await fetchone(
        """SELECT debt_amount
           FROM credit_card_events
           WHERE user_id=%s AND card_id=%s
             AND event_type IN ('setup', 'balance_update')
             AND event_date < %s
           ORDER BY event_date DESC, id DESC
           LIMIT 1""",
        (user_id, card_id, month_start),
    )
    if not start_row:
        start_row = await fetchone(
            """SELECT debt_amount
               FROM credit_card_events
               WHERE user_id=%s AND card_id=%s
                 AND event_type IN ('setup', 'balance_update')
                 AND event_date >= %s
               ORDER BY event_date, id
               LIMIT 1""",
            (user_id, card_id, month_start),
        )

    topups_row = await fetchone(
        """SELECT COALESCE(SUM(amount), 0)
           FROM credit_card_events
           WHERE user_id=%s AND card_id=%s
             AND event_type='topup'
             AND event_date >= %s""",
        (user_id, card_id, month_start),
    )

    start_debt = float(start_row[0]) if start_row else float(card[2] or 0)
    end_debt = float(card[2] or 0)
    topups = float(topups_row[0] or 0) if topups_row else 0.0
    debt_delta = start_debt - end_debt
    new_card_spending = end_debt - start_debt + topups

    return {
        "month_start": month_start,
        "start_debt": start_debt,
        "end_debt": end_debt,
        "topups": topups,
        "debt_delta": debt_delta,
        "new_card_spending": new_card_spending,
    }
