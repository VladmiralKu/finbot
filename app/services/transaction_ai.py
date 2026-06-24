import re
from datetime import date

from app.parser import parse_quick_input
from app.services.category_matcher import match_category


INCOME_WORDS = (
    "зарплат", "получил", "получила", "пришло", "доход", "заработал",
    "заработала", "выручка", "аванс", "перевели", "начислили", "продал",
)

EXPENSE_WORDS = (
    "купил", "купила", "потратил", "потратила", "оплатил", "оплатила",
    "заплатил", "заплатила", "списали", "расход", "проиграл", "проиграла",
)


def _guess_type(text: str, explicit_type: str | None = None) -> str:
    if explicit_type in ("expense", "income"):
        return explicit_type
    lower = text.lower()
    if any(word in lower for word in INCOME_WORDS):
        return "income"
    if any(word in lower for word in EXPENSE_WORDS):
        return "expense"
    return "expense"


def _find_amount(text: str) -> float | None:
    match = re.search(r"(?<!\d)([+-]?\d+(?:[.,]\d{1,2})?)(?!\d)", text)
    if not match:
        return None
    try:
        return abs(float(match.group(1).replace(",", ".").lstrip("+-")))
    except ValueError:
        return None


def _strip_amount_words(text: str) -> str:
    text = re.sub(r"(?<!\d)[+-]?\d+(?:[.,]\d{1,2})?(?!\d)", " ", text, count=1)
    text = re.sub(r"\b(рублей|рубля|руб|р|₽|тысяч|тыс)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(за|на|в|по|и|а)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


async def extract_transactions_from_text(user_id: int, text: str, source: str) -> list[dict]:
    parsed = parse_quick_input(text)
    if parsed and parsed.get("amount"):
        tx_type = _guess_type(text, parsed.get("type"))
        category = await match_category(
            user_id,
            text,
            type_hint=tx_type,
            amount=parsed["amount"],
            source=source,
        )
        if not category:
            return []
        return [
            {
                "amount": parsed["amount"],
                "type": category["type"],
                "category_id": category["category_id"],
                "category_name": category["category_name"],
                "kind": category["kind"],
                "comment": parsed.get("comment") or _strip_amount_words(text),
                "transaction_date": parsed.get("transaction_date") or date.today(),
                "pnl_period": parsed.get("pnl_period"),
            }
        ]

    amount = _find_amount(text)
    if amount is None:
        return []

    tx_type = _guess_type(text)
    category = await match_category(
        user_id,
        text,
        type_hint=tx_type,
        amount=amount,
        source=source,
    )
    if not category:
        return []

    return [
        {
            "amount": amount,
            "type": category["type"],
            "category_id": category["category_id"],
            "category_name": category["category_name"],
            "kind": category["kind"],
            "comment": _strip_amount_words(text),
            "transaction_date": date.today(),
            "pnl_period": None,
        }
    ]
