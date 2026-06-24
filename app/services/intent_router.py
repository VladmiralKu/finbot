import re
from datetime import date

from app.services.category_commands import parse_category_command
from app.services.transaction_ai import extract_transactions_from_text


MONTHS = {
    "январь": 1, "января": 1,
    "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7,
    "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11,
    "декабрь": 12, "декабря": 12,
}


def _month_from_text(text: str) -> tuple[int, int] | None:
    lower = text.lower()
    year_match = re.search(r"\b(20\d{2})\b", lower)
    year = int(year_match.group(1)) if year_match else date.today().year
    for name, month in MONTHS.items():
        if name in lower:
            return year, month
    return None


async def parse_user_intent(user_id: int, text: str, source: str) -> dict:
    lower = (text or "").lower().strip()

    if lower in ("меню", "🏠 меню", "/start") or "главное меню" in lower:
        return {
            "intent": "open_main_menu",
            "confidence": 0.98,
            "params": {},
            "needs_confirmation": False,
        }

    if "отчет" in lower or "отчёт" in lower:
        period = _month_from_text(lower)
        if period:
            year, month = period
        else:
            today = date.today()
            year, month = today.year, today.month
        return {
            "intent": "show_report",
            "confidence": 0.9,
            "params": {"year": year, "month": month},
            "needs_confirmation": False,
        }

    if "последн" in lower and ("транзакц" in lower or "операц" in lower):
        return {
            "intent": "show_recent",
            "confidence": 0.9,
            "params": {},
            "needs_confirmation": False,
        }

    if "календар" in lower or "регулярн" in lower:
        return {
            "intent": "show_calendar",
            "confidence": 0.88,
            "params": {},
            "needs_confirmation": False,
        }

    category_like = (
        "категор" in lower
        or "переимен" in lower
        or "замени" in lower
        or "заменить" in lower
        or "постоянн" in lower
        or "переменн" in lower
    )
    if category_like:
        for scope in ("expense", "income"):
            command = await parse_category_command(user_id, text, scope)
            if command and command.get("intent") != "unknown":
                return {
                    "intent": command["intent"],
                    "confidence": command.get("confidence", 0.8),
                    "params": {"scope_type": scope, "command": command},
                    "needs_confirmation": False,
                }

    if "удали" in lower or "удалить" in lower:
        tx_id = None
        match = re.search(r"(?:#|транзакци[яюи]\s*)?(\d+)", lower)
        if match:
            tx_id = int(match.group(1))
        return {
            "intent": "delete_transaction",
            "confidence": 0.92,
            "params": {"tx_id": tx_id, "last": "последн" in lower},
            "needs_confirmation": True,
        }

    transactions = await extract_transactions_from_text(user_id, text, source)
    if transactions:
        return {
            "intent": "add_transaction",
            "confidence": 0.86,
            "params": {"transactions": transactions},
            "needs_confirmation": False,
        }

    return {
        "intent": "unknown",
        "confidence": 0.0,
        "params": {},
        "needs_confirmation": False,
    }
