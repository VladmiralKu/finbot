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


RECURRING_SEARCH_STOP_WORDS = {
    "удали", "удалить", "убери", "убрать", "отмени", "отменить",
    "измени", "изменить", "перенеси", "перенести", "поменяй", "поменять",
    "исправь", "исправить", "сделай", "поставь", "платеж", "платежа",
    "платежи", "платежей", "платежом", "платёж", "платёжа", "платёжи",
    "регулярный", "регулярного", "регулярные", "регулярных", "календарь",
    "календаря", "сумма", "сумму", "суммы", "на", "в", "во", "из", "с",
    "со", "по", "для", "у", "и", "или", "от", "до", "каждый", "каждую",
    "каждое", "каждые", "месяц", "месяца", "месяцу", "ежемесячно",
    "раз", "неделю", "неделя", "число", "числа", "руб", "рублей",
    "рубля", "рубль", "примерно", "около",
    *WEEKDAYS.keys(),
}


def _stem_word(word: str) -> str:
    word = re.sub(r"[^а-яёa-z0-9]+", "", (word or "").lower().replace("ё", "е"))
    suffixes = (
        "иями", "ями", "ами", "ого", "ему", "ыми", "ими", "ую", "юю",
        "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем", "ах", "ях",
        "ой", "ей", "ов", "ев", "ам", "ям", "а", "у", "ы", "и", "е",
        "о", "й", "ь",
    )
    for suffix in suffixes:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def _recurring_search_terms(text: str) -> list[str]:
    lower = (text or "").lower().replace("ё", "е")
    lower = re.sub(r"~?\d[\d\s]*(?:[,.]\d+)?\s*(?:рублей|рубля|рубль|рубл|руб|р|₽)?", " ", lower)
    terms = []
    for word in re.findall(r"[а-яёa-z0-9]+", lower):
        if word in RECURRING_SEARCH_STOP_WORDS or word.isdigit():
            continue
        stem = _stem_word(word)
        if stem and stem not in RECURRING_SEARCH_STOP_WORDS and stem not in terms:
            terms.append(stem)
    return terms


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
            re.search(r"(?:каждый месяц|ежемесячно|раз в месяц)\s*(?:по\s*)?(\d{1,2})(?:-?го|\s+числ[ао])?", lower)
            or re.search(r"\b(\d{1,2})(?:-?го|\s+числ[ао])\b", lower)
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


def parse_recurring_edit(text: str) -> dict | None:
    lower = (text or "").lower()
    parsed = {
        "amount": None,
        "is_approx": None,
        "repeat_type": None,
        "repeat_day_month": None,
        "repeat_day_week": None,
    }

    amount_match = re.search(
        r"(?:сумм[ауы]?\s*(?:на)?\s*)?(~?\d[\d\s]*(?:[,.]\d+)?)\s*(?:р|руб|рубл|₽)",
        lower,
    )
    if amount_match:
        raw_amount = amount_match.group(1)
        parsed["amount"] = float(raw_amount.replace("~", "").replace(" ", "").replace(",", "."))
        parsed["is_approx"] = "~" in raw_amount or "примерн" in lower or "около" in lower

    if "каждый месяц" in lower or "ежемесяч" in lower or "раз в месяц" in lower or "числа" in lower or "число" in lower:
        parsed["repeat_type"] = "monthly"
        day_match = (
            re.search(r"(?:каждый месяц|ежемесячно|раз в месяц)\s*(?:по\s*)?(\d{1,2})(?:-?го|\s+числ[ао])?", lower)
            or re.search(r"\b(\d{1,2})(?:-?го|\s+числ[ао])\b", lower)
        )
        if day_match:
            parsed["repeat_day_month"] = max(1, min(28, int(day_match.group(1))))
    else:
        for name, weekday in WEEKDAYS.items():
            if name in lower:
                parsed["repeat_type"] = "weekly"
                parsed["repeat_day_week"] = weekday
                break

    if any(value is not None for value in parsed.values()):
        return parsed
    return None


async def find_recurring_payment(user_id: int, text: str):
    payments = await get_recurring_payments(user_id)
    if not payments:
        return None, []

    query_terms = _recurring_search_terms(text)
    query = " ".join(query_terms)

    scored = []
    for payment in payments:
        name = str(payment[2] or "")
        name_terms = _recurring_search_terms(name)
        name_query = " ".join(name_terms)
        if query and name_query and (query in name_query or name_query in query):
            score = 1.0
        elif query_terms and name_terms:
            common = set(query_terms) & set(name_terms)
            overlap = len(common) / max(1, min(len(query_terms), len(name_terms)))
            ratio = difflib.SequenceMatcher(None, query, name_query).ratio()
            score = max(overlap, ratio)
        else:
            score = difflib.SequenceMatcher(None, query, name.lower()).ratio() if query else 0.0
        scored.append((score, payment))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.62:
        return scored[0][1], []
    return None, [payment for _, payment in scored[:5]]
