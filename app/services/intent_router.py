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


def _looks_like_category_change(lower: str) -> bool:
    action_like = (
        "помен" in lower
        or "измени" in lower
        or "изменить" in lower
        or "смен" in lower
        or "перенеси" in lower
        or "перенести" in lower
    )
    target_like = (
        "категор" in lower
        or "стать" in lower
        or "раздел" in lower
        or re.search(r"(?:^|\s)(?:#\d+|\d{2,})(?:\s|$)", lower)
        or "транзакц" in lower
        or "операц" in lower
    )
    destination_like = re.search(r"\b(?:на|в)\s+[а-яёa-z0-9 /-]{2,}$", lower)
    return bool(action_like and target_like and destination_like)


def _has_transaction_pointer(lower: str) -> bool:
    return bool(
        "транзакц" in lower
        or "операц" in lower
        or re.search(r"(?:^|\s)#\d+(?:\s|$)", lower)
        or re.search(r"\d[\d\s]*(?:[,.]\d+)?\s*(?:р|руб|рубл)", lower)
        or re.search(r"\b(?:сумм[ауы]?|номер)\s+\d", lower)
    )


def _is_lookup_request(lower: str) -> bool:
    lookup_markers = (
        "найди", "найти", "покажи", "показать", "посмотри", "посмотреть",
        "сколько", "какие", "какой", "какая", "где", "когда", "отчет", "отчёт",
        "анализ", "проанализ", "с копейками",
    )
    return any(marker in lower for marker in lookup_markers)


def _is_explicit_transaction_request(lower: str, source: str) -> bool:
    if source not in ("ai_chat", "ai_voice"):
        return True
    if _is_lookup_request(lower):
        return False
    action_markers = (
        "внеси", "внести", "запиши", "записать", "добавь", "добавить",
        "потратил", "потратила", "потрачено", "купил", "купила", "оплатил",
        "оплатила", "заработал", "заработала", "получил", "получила",
        "доход", "расход",
    )
    return any(marker in lower for marker in action_markers)


def _looks_like_category_or_article_edit(lower: str) -> bool:
    action_like = (
        "замени" in lower
        or "заменить" in lower
        or "запени" in lower
        or "помен" in lower
        or "смени" in lower
        or "сменить" in lower
        or "переимен" in lower
    )
    object_like = "категор" in lower or "стать" in lower or "раздел" in lower
    return action_like and object_like


def _looks_like_transaction_edit(lower: str) -> bool:
    action_like = (
        "измени" in lower
        or "изменить" in lower
        or "исправ" in lower
        or "помен" in lower
        or "смени" in lower
        or "перенеси" in lower
        or "перенести" in lower
        or "поставь" in lower
    )
    field_like = (
        "дат" in lower
        or "сумм" in lower
        or "коммент" in lower
        or "описан" in lower
        or "категор" in lower
        or "стать" in lower
    )
    comment_like = (
        "коммент" in lower
        or "описан" in lower
        or "подпись" in lower
        or "заметк" in lower
    )
    object_like = (
        "транзакц" in lower
        or "операц" in lower
        or _has_transaction_pointer(lower)
        or comment_like
    )
    return action_like and field_like and object_like


def _looks_like_beautiful_report(lower: str) -> bool:
    report_like = "отчет" in lower or "отчёт" in lower
    beautiful_like = (
        "красив" in lower
        or "картин" in lower
        or "изображ" in lower
        or "инфограф" in lower
        or "визуаль" in lower
        or "pdf" in lower
    )
    return report_like and beautiful_like


def _looks_like_add_recurring(lower: str) -> bool:
    add_like = (
        "добав" in lower
        or "созд" in lower
        or "постав" in lower
        or "заплан" in lower
    )
    recurring_like = (
        "каждый месяц" in lower
        or "ежемесяч" in lower
        or "раз в месяц" in lower
        or "платеж" in lower
        or "платёж" in lower
        or "календар" in lower
        or "регуляр" in lower
    )
    return add_like and recurring_like


def _looks_like_delete_recurring(lower: str) -> bool:
    delete_like = "удали" in lower or "удалить" in lower or "убери" in lower or "убрать" in lower or "отмени" in lower
    recurring_like = "платеж" in lower or "платёж" in lower or "календар" in lower or "регуляр" in lower or "подписк" in lower
    return delete_like and recurring_like


def _looks_like_edit_recurring(lower: str) -> bool:
    transaction_specific = (
        "транзакц" in lower
        or "операц" in lower
        or "коммент" in lower
        or "описан" in lower
        or "категор" in lower
        or "стать" in lower
    )
    if transaction_specific:
        return False

    edit_like = (
        "измени" in lower
        or "изменить" in lower
        or "перенеси" in lower
        or "перенести" in lower
        or "поменяй" in lower
        or "поменять" in lower
        or "исправь" in lower
        or "исправить" in lower
        or "сделай" in lower
        or "поставь" in lower
    )
    recurring_like = (
        "платеж" in lower
        or "платёж" in lower
        or "календар" in lower
        or "регуляр" in lower
        or "подписк" in lower
    )
    field_like = (
        "сумм" in lower
        or "руб" in lower
        or "₽" in lower
        or "числа" in lower
        or "каждый месяц" in lower
        or "ежемесяч" in lower
        or "раз в месяц" in lower
        or any(day in lower for day in ("понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскрес"))
    )
    return edit_like and (recurring_like or field_like)


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
        if _looks_like_beautiful_report(lower):
            return {
                "intent": "beautiful_report",
                "confidence": 0.9,
                "params": {"year": year, "month": month},
                "needs_confirmation": False,
            }
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

    if _looks_like_add_recurring(lower):
        return {
            "intent": "add_recurring_payment",
            "confidence": 0.9,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    if _looks_like_delete_recurring(lower):
        return {
            "intent": "delete_recurring_payment",
            "confidence": 0.9,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    if _looks_like_edit_recurring(lower):
        return {
            "intent": "edit_recurring_payment",
            "confidence": 0.9,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    if "календар" in lower or "регулярн" in lower:
        return {
            "intent": "show_calendar",
            "confidence": 0.88,
            "params": {},
            "needs_confirmation": False,
        }

    if _looks_like_transaction_edit(lower):
        return {
            "intent": "edit_transaction",
            "confidence": 0.88,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    if _looks_like_category_change(lower) and _has_transaction_pointer(lower):
        return {
            "intent": "change_transaction_category",
            "confidence": 0.86,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    if _looks_like_category_or_article_edit(lower) and not _has_transaction_pointer(lower):
        return {
            "intent": "clarify_category_or_transaction_edit",
            "confidence": 0.78,
            "params": {"text": text},
            "needs_confirmation": True,
        }

    category_like = (
        "категор" in lower
        or "переимен" in lower
        or "замени" in lower
        or "заменить" in lower
        or "запени" in lower
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
            "params": {"tx_id": tx_id, "last": "последн" in lower, "text": text},
            "needs_confirmation": True,
        }

    transactions = []
    if _is_explicit_transaction_request(lower, source):
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
