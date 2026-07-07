import difflib
import re

from app.database import get_recurring_payments


WEEKDAYS = {
    "понедельник": 0,
    "понедельникам": 0,
    "вторник": 1,
    "вторникам": 1,
    "среда": 2,
    "среду": 2,
    "средам": 2,
    "четверг": 3,
    "четвергам": 3,
    "пятница": 4,
    "пятницу": 4,
    "пятницам": 4,
    "суббота": 5,
    "субботу": 5,
    "субботам": 5,
    "воскресенье": 6,
    "воскресеньям": 6,
}


def _clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip(" .,!?:;"))
    return value[:1].upper() + value[1:] if value else value


def parse_recurring_add(text: str) -> dict | None:
    lower = (text or "").lower()
    amount_match = re.search(r"(~?\d[\d\s]*(?:[,.]\d+)?)\s*(?:р|руб|рубл|₽)?", lower)
    if not amount_match:
        return None

    raw_amount = amount_match.group(1)
    amount = float(raw_amount.replace("~", "").replace(" ", "").replace(",", "."))
    is_approx = "~" in raw_amount or "примерн" in lower or "около" in lower

    repeat_type = None
    day_month = None
    day_week = None
    if "каждый месяц" in lower or "ежемесяч" in lower or "раз в месяц" in lower:
        repeat_type = "monthly"
        day_match = (
            re.search(r"(?:каждый месяц|ежемесячно|раз в месяц)\s*(?:по\s*)?(\d{1,2})(?:-?го|\s+числа)?", lower)
            or re.search(r"\b(\d{1,2})(?:-?го|\s+числа)\b", lower)
        )
        if day_match:
            day_month = max(1, min(28, int(day_match.group(1))))
        else:
            day_month = 1
    else:
        for name, weekday in WEEKDAYS.items():
            if name in lower:
                repeat_type = "weekly"
                day_week = weekday
                break

    if not repeat_type:
        return None

    before_amount = text[:amount_match.start()].strip()
    name = re.sub(
        r"\b(добавь|добавить|создай|создать|поставь|запланируй|запланировать|в\s+календарь|плат[её]ж)\b",
        " ",
        before_amount,
        flags=re.IGNORECASE,
    )
    name = _clean_name(name)
    if not name:
        name = "Регулярный платёж"

    return {
        "name": name,
        "amount": amount,
        "is_approx": is_approx,
        "repeat_type": repeat_type,
        "repeat_day_month": day_month,
        "repeat_day_week": day_week,
    }


async def find_recurring_payment(user_id: int, text: str):
    payments = await get_recurring_payments(user_id)
    if not payments:
        return None, []

    lower = (text or "").lower()
    query = re.sub(
        r"\b(удали|удалить|убери|убрать|отмени|отменить|плат[её]ж|регулярн\w*|из\s+календаря)\b",
        " ",
        lower,
    )
    query = re.sub(r"\s+", " ", query).strip()

    scored = []
    for payment in payments:
        name = str(payment[2] or "")
        name_lower = name.lower()
        if query and (query in name_lower or name_lower in query):
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, query, name_lower).ratio() if query else 0.0
        scored.append((score, payment))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.72:
        return scored[0][1], []
    return None, [payment for _, payment in scored[:5]]
