import re
from datetime import date

from app.parser import parse_quick_input
from app.services.category_matcher import match_category


INCOME_WORDS = (
    "зарплат", "получил", "получила", "получили", "получен", "пришло",
    "пришла", "поступ", "доход", "заработ", "выруч", "аванс", "перевели",
    "начислили", "продал", "продала", "продаж", "оплатили", "выплат",
    "гонорар", "дивиденд", "кэшбек", "кешбек", "возврат", "прибыл",
)

EXPENSE_WORDS = (
    "купил", "купила", "потратил", "потратила", "оплатил", "оплатила",
    "заплатил", "заплатила", "списали", "расход", "проиграл", "проиграла",
    "покуп", "трата", "трат", "платеж", "платёж",
)


AMOUNT_RE = re.compile(r"(?<!\d)([+-]?\d+(?:[.,]\d{1,2})?)(?!\d)")
CONNECTOR_RE = re.compile(r"\b(?:и|а|потом|затем|далее|ещ[её]|плюс)\b|[,;]", re.IGNORECASE)


def _guess_type(text: str, explicit_type: str | None = None, amount_token: str | None = None) -> str:
    if amount_token:
        stripped = amount_token.strip()
        if stripped.startswith("+"):
            return "income"
        if stripped.startswith("-"):
            return "expense"
    if explicit_type in ("expense", "income"):
        return explicit_type
    lower = text.lower()
    if any(word in lower for word in INCOME_WORDS):
        return "income"
    if any(word in lower for word in EXPENSE_WORDS):
        return "expense"
    return "expense"


def _find_amount(text: str) -> float | None:
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    try:
        return abs(float(match.group(1).replace(",", ".").lstrip("+-")))
    except ValueError:
        return None


def _strip_amount_words(text: str) -> str:
    text = AMOUNT_RE.sub(" ", text, count=1)
    text = re.sub(r"\b(рублей|рубля|руб|р|₽|тысяч|тыс)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(за|на|в|по|и|а)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _parse_amount(value: str) -> float | None:
    try:
        return abs(float(value.replace(",", ".").lstrip("+-")))
    except ValueError:
        return None


def _has_type_marker(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in INCOME_WORDS + EXPENSE_WORDS)


def _is_probable_year(token: str, full_text: str) -> bool:
    if token.startswith(("+", "-")):
        return False
    try:
        number = int(float(token.replace(",", ".")))
    except ValueError:
        return False
    if not 1900 <= number <= 2100:
        return False
    return any(month in full_text.lower() for month in (
        "январ", "феврал", "март", "апрел", "ма", "июн", "июл",
        "август", "сентябр", "октябр", "ноябр", "декабр",
    ))


def _amount_matches(text: str):
    matches = []
    for match in AMOUNT_RE.finditer(text):
        token = match.group(1)
        if _is_probable_year(token, text):
            continue
        matches.append(match)
    return matches


def _left_context(text: str, previous_end: int | None, current_start: int) -> str:
    raw = text[:current_start] if previous_end is None else text[previous_end:current_start]
    if previous_end is not None:
        parts = CONNECTOR_RE.split(raw)
        raw = parts[-1] if parts else ""
        if not _has_type_marker(raw):
            return ""
    return raw.strip()


def _right_context(text: str, current_end: int, next_start: int | None) -> str:
    raw = text[current_end:] if next_start is None else text[current_end:next_start]
    if next_start is not None:
        parts = CONNECTOR_RE.split(raw, maxsplit=1)
        raw = parts[0] if parts else ""
    return raw.strip()


def _candidate_segments(text: str) -> list[tuple[str, str]]:
    matches = _amount_matches(text)
    if len(matches) <= 1:
        return []

    result = []
    for index, match in enumerate(matches):
        previous_end = matches[index - 1].end() if index > 0 else None
        next_start = matches[index + 1].start() if index + 1 < len(matches) else None
        left = _left_context(text, previous_end, match.start())
        right = _right_context(text, match.end(), next_start)
        segment = re.sub(r"\s+", " ", f"{left} {match.group(1)} {right}").strip()
        result.append((segment, match.group(1)))
    return result


async def _build_transaction(
    user_id: int,
    text: str,
    amount: float,
    tx_type: str,
    source: str,
    transaction_date=None,
    pnl_period: str | None = None,
) -> dict | None:
    category = await match_category(
        user_id,
        text,
        type_hint=tx_type,
        amount=amount,
        source=source,
    )
    if not category:
        return None
    return {
        "amount": amount,
        "type": category["type"],
        "category_id": category["category_id"],
        "category_name": category["category_name"],
        "kind": category["kind"],
        "comment": _strip_amount_words(text),
        "transaction_date": transaction_date or date.today(),
        "pnl_period": pnl_period,
    }


async def extract_transactions_from_text(user_id: int, text: str, source: str) -> list[dict]:
    multi_transactions = []
    for segment, amount_token in _candidate_segments(text):
        amount = _parse_amount(amount_token)
        if amount is None:
            continue
        tx_type = _guess_type(segment, amount_token=amount_token)
        tx = await _build_transaction(user_id, segment, amount, tx_type, source)
        if tx:
            multi_transactions.append(tx)
    if multi_transactions:
        return multi_transactions

    parsed = parse_quick_input(text)
    if parsed and parsed.get("amount"):
        explicit_type = parsed.get("type") if parsed.get("sign_explicit") else None
        amount_token = "+" if parsed.get("type") == "income" and parsed.get("sign_explicit") else None
        if parsed.get("type") == "expense" and parsed.get("sign_explicit"):
            amount_token = "-"
        tx_type = _guess_type(text, explicit_type, amount_token=amount_token)
        tx = await _build_transaction(
            user_id,
            text,
            parsed["amount"],
            tx_type,
            source,
            transaction_date=parsed.get("transaction_date"),
            pnl_period=parsed.get("pnl_period"),
        )
        if not tx:
            return []
        if parsed.get("comment"):
            tx["comment"] = parsed["comment"]
        return [tx]

    amount = _find_amount(text)
    if amount is None:
        return []

    tx_type = _guess_type(text)
    tx = await _build_transaction(user_id, text, amount, tx_type, source)
    if not tx:
        return []
    return [tx]
