import json
import os
import re
from datetime import date, timedelta

import httpx

KIE_API_BASE = "https://api.kie.ai"
KIE_IMAGE_MODEL = os.getenv("KIE_IMAGE_MODEL", "gpt-image-2-text-to-image")
KIE_REPORT_ASPECT_RATIO = os.getenv("KIE_REPORT_ASPECT_RATIO", "2:3")

REPORT_MONTHS = (
    ("января", 1), ("январь", 1),
    ("февраля", 2), ("февраль", 2),
    ("марта", 3), ("март", 3),
    ("апреля", 4), ("апрель", 4),
    ("мая", 5), ("май", 5),
    ("июня", 6), ("июнь", 6),
    ("июля", 7), ("июль", 7),
    ("августа", 8), ("август", 8),
    ("сентября", 9), ("сентябрь", 9),
    ("октября", 10), ("октябрь", 10),
    ("ноября", 11), ("ноябрь", 11),
    ("декабря", 12), ("декабрь", 12),
)
NOTE_EXCLUDE_MARKERS = ("без замет", "убери замет", "не добавляй замет", "не показывай замет")
NOTE_INCLUDE_MARKERS = ("с замет", "добавь замет", "учти замет", "используй замет", "по замет")
TEXT_LIMIT = 90


def _rub(value) -> str:
    return f"{float(value or 0):,.0f}".replace(",", " ") + " ₽"


def _month_title(year: int, month: int) -> str:
    names = {
        1: "январь", 2: "февраль", 3: "март", 4: "апрель",
        5: "май", 6: "июнь", 7: "июль", 8: "август",
        9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
    }
    return names.get(month, str(month)) + " " + str(year)


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _safe_date(year: int, month: int, day: int) -> date:
    month = max(1, min(12, month))
    return date(year, month, min(day, _last_day(year, month).day))


def _month_number(value: str | None) -> int | None:
    lower = (value or "").lower()
    for name, number in REPORT_MONTHS:
        if lower.startswith(name):
            return number
    return None


def _mentioned_months(text: str) -> list[int]:
    lower = (text or "").lower()
    found = []
    for name, number in REPORT_MONTHS:
        for match in re.finditer(r"\b" + re.escape(name) + r"\b", lower):
            found.append((match.start(), number))
    result = []
    for _, number in sorted(found):
        if number not in result:
            result.append(number)
    return result


def _date_range_from_prompt(user_prompt: str | None, year: int, month: int) -> tuple[date, date]:
    lower = (user_prompt or "").lower()
    default_start = date(year, month, 1)
    default_end = _last_day(year, month)

    relative_days = re.search(
        r"\b(?:за\s+)?последн(?:ие|их|ий|ую)?\s+(\d{1,3})\s+"
        r"(?:дн(?:я|ей)?|день)\b",
        lower,
    )
    if relative_days:
        days = max(1, min(120, int(relative_days.group(1))))
        end = date.today()
        return end - timedelta(days=days - 1), end

    word_range = re.search(
        r"\bс\s+(\d{1,2})\s+([а-яё]+)(?:\s+(20\d{2}))?\s+"
        r"(?:по|до)\s+(\d{1,2})\s+([а-яё]+)(?:\s+(20\d{2}))?",
        lower,
    )
    if word_range:
        start_month = _month_number(word_range.group(2))
        end_month = _month_number(word_range.group(5))
        if start_month and end_month:
            base_year = int(word_range.group(3) or word_range.group(6) or year)
            start = _safe_date(
                int(word_range.group(3) or base_year),
                start_month,
                int(word_range.group(1)),
            )
            end = _safe_date(
                int(word_range.group(6) or base_year),
                end_month,
                int(word_range.group(4)),
            )
            if start > end and not word_range.group(6):
                end = end.replace(year=end.year + 1)
            return start, end

    numeric_range = re.search(
        r"\bс\s+(\d{1,2})[./-](\d{1,2})(?:[./-](20\d{2}))?\s+"
        r"(?:по|до)\s+(\d{1,2})[./-](\d{1,2})(?:[./-](20\d{2}))?",
        lower,
    )
    if numeric_range:
        base_year = int(numeric_range.group(3) or numeric_range.group(6) or year)
        start = _safe_date(
            int(numeric_range.group(3) or base_year),
            int(numeric_range.group(2)),
            int(numeric_range.group(1)),
        )
        end = _safe_date(
            int(numeric_range.group(6) or base_year),
            int(numeric_range.group(5)),
            int(numeric_range.group(4)),
        )
        if start > end and not numeric_range.group(6):
            end = end.replace(year=end.year + 1)
        return start, end

    months = _mentioned_months(lower)
    if len(months) >= 2 and any(marker in lower for marker in ("сравн", "соотнош", "динамик", "помесяч")):
        ordered = sorted(set(months))
        return date(year, ordered[0], 1), _last_day(year, ordered[-1])

    return default_start, default_end


def _short(value, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _date_label(value) -> str:
    return value.strftime("%d.%m.%Y")


def _wants_notes(user_prompt: str | None) -> bool:
    lower = (user_prompt or "").lower()
    if any(marker in lower for marker in NOTE_EXCLUDE_MARKERS):
        return False
    return any(marker in lower for marker in NOTE_INCLUDE_MARKERS)


def _join_lines(lines: list[str], empty: str = "- нет данных") -> str:
    return "\n".join(lines or [empty])


FOCUS_STOP_WORDS = {
    "должно", "быть", "между", "деньги", "денег", "деньгами", "расход",
    "расходом", "расходами", "расходов", "поступлениями", "поступление",
    "поступлений", "только", "тот", "ко", "график", "цифры", "проценты",
    "процентах", "красивый", "отчет", "отчёт", "последние", "последних",
    "дней", "дня", "день", "соотношение", "соотношения",
}


def _words(value: str | None) -> set[str]:
    return {
        word
        for word in re.findall(r"[а-яёa-z0-9]+", (value or "").lower())
        if len(word) > 2 and word not in FOCUS_STOP_WORDS
    }


def _extract_expense_focus_words(user_prompt: str | None) -> set[str]:
    lower = (user_prompt or "").lower()
    patterns = (
        r"(?:расход(?:ом|ами|ов|ы|а)?|траты|тратами)\s+на\s+([а-яёa-z0-9 /-]+)",
        r"(?:расход(?:ом|ами|ов|ы|а)?|траты|тратами)\s+по\s+([а-яёa-z0-9 /-]+)",
        r"категори[яю]\s+([а-яёa-z0-9 /-]+)",
    )
    chunks = []
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            chunk = match.group(1)
            chunk = re.split(
                r"\b(?:должно|только|тот\s+ко|график|цифры|процент|соотнош|за\s+последн|и\s+поступ)\b",
                chunk,
                maxsplit=1,
            )[0]
            chunks.append(chunk)
    if chunks:
        return set().union(*(_words(chunk) for chunk in chunks))
    return set()


def _wants_only_graph(user_prompt: str | None) -> bool:
    lower = (user_prompt or "").lower()
    graph_like = "график" in lower or "диаграм" in lower or "визуал" in lower
    only_like = "только" in lower or "тот ко" in lower or "ток " in lower
    return graph_like and only_like


def _wants_percentages(user_prompt: str | None) -> bool:
    lower = (user_prompt or "").lower()
    return "процент" in lower or "%" in lower or "дол" in lower or "соотнош" in lower


def _matching_expense_category_names(categories, user_prompt: str | None) -> list[str]:
    focus_words = _extract_expense_focus_words(user_prompt)
    if not focus_words:
        return []

    matches = []
    for name, tx_type, _total in categories:
        if tx_type != "expense":
            continue
        name_words = _words(str(name))
        if not name_words:
            continue
        overlap = focus_words & name_words
        if len(overlap) >= min(2, len(focus_words)) or focus_words <= name_words:
            matches.append(str(name))
    return matches


def _build_focus_block(
    user_prompt: str | None,
    summary: dict,
    categories,
    daily_rows,
    category_daily_rows,
) -> str:
    focus_names = _matching_expense_category_names(categories, user_prompt)
    if not focus_names:
        if _extract_expense_focus_words(user_prompt):
            return (
                "Фокус запроса:\n"
                "- Пользователь просит отдельный расходный срез, но подходящая категория не найдена в данных.\n"
                "- В отчёте явно покажи: данных по нужной категории нет.\n"
            )
        return ""

    focus_name_set = {name.lower() for name in focus_names}
    focus_total = sum(
        float(total or 0)
        for name, tx_type, total in categories
        if tx_type == "expense" and str(name).lower() in focus_name_set
    )
    income_total = float(summary.get("income") or 0)
    expense_total = float(summary.get("total_expense") or 0)
    income_ratio = (focus_total / income_total * 100) if income_total else None
    expense_ratio = (focus_total / expense_total * 100) if expense_total else None

    income_by_day = {row[0]: float(row[1] or 0) for row in daily_rows}
    focus_by_day = {}
    for row_date, name, tx_type, total in category_daily_rows:
        if tx_type == "expense" and str(name).lower() in focus_name_set:
            focus_by_day[row_date] = focus_by_day.get(row_date, 0.0) + float(total or 0)

    focus_daily_lines = []
    for day in sorted(set(income_by_day) | set(focus_by_day)):
        day_income = income_by_day.get(day, 0.0)
        day_focus = focus_by_day.get(day, 0.0)
        day_ratio = day_focus / day_income * 100 if day_income else None
        ratio_text = f"{day_ratio:.1f}%" if day_ratio is not None else "нет поступлений"
        focus_daily_lines.append(
            f"- {_date_label(day)}: поступления {_rub(day_income)}, "
            f"{', '.join(focus_names)} {_rub(day_focus)}, доля {ratio_text}"
        )

    income_ratio_text = f"{income_ratio:.1f}%" if income_ratio is not None else "нет поступлений"
    expense_ratio_text = f"{expense_ratio:.1f}%" if expense_ratio is not None else "нет расходов"
    return (
        "Фокус запроса, использовать как главный и единственный смысл отчёта:\n"
        f"- Сравниваем все поступления денег и расход на: {', '.join(focus_names)}.\n"
        f"- Поступления за период: {_rub(income_total)}.\n"
        f"- Расход на {', '.join(focus_names)}: {_rub(focus_total)}.\n"
        f"- Расход на {', '.join(focus_names)} / поступления: {income_ratio_text}.\n"
        f"- Расход на {', '.join(focus_names)} / все расходы: {expense_ratio_text}.\n"
        "Динамика фокуса по дням:\n"
        + _join_lines(focus_daily_lines)
        + "\n"
    )


async def _period_summary(user_id: int, start_date: date, end_date: date) -> dict:
    from app.database import fetchall

    rows = await fetchall(
        """SELECT type, SUM(amount)
           FROM transactions
           WHERE user_id=%s AND transaction_date BETWEEN %s AND %s
           GROUP BY type""",
        (user_id, start_date, end_date),
    )
    result = {"income": 0.0, "total_expense": 0.0}
    for row in rows:
        if row[0] == "income":
            result["income"] += float(row[1] or 0)
        elif row[0] == "expense":
            result["total_expense"] += float(row[1] or 0)
    result["balance"] = result["income"] - result["total_expense"]
    return result


def is_default_beautiful_report_request(user_prompt: str | None) -> bool:
    lower = (user_prompt or "").lower()
    custom_markers = (
        "график", "диаграм", "соотнош", "сравн", "сравни", "только", "без ",
        "убери", "добавь", "пики", "пик ", "лучших дней", "котик", "котиков",
        "закуп", "зарплат", "выруч", "поступлен", "категор", "кусок", "часть",
        "в виде", "по дням", "по недел", "отдельно", "последн", "процент",
        "возврат", "долг",
    )
    return not any(marker in lower for marker in custom_markers)


def describe_report_plan(
    year: int,
    month: int,
    user_prompt: str | None = None,
) -> str:
    requested = _short(user_prompt or "Сделай красивый финансовый отчёт.", 700)
    start_date, end_date = _date_range_from_prompt(user_prompt, year, month)
    lower = (user_prompt or "").lower()
    focus = []
    if "соотнош" in lower or "сравн" in lower:
        focus.append("сравнение и доли между указанными категориями/доходами")
    if "график" in lower or "динамик" in lower or "поступлен" in lower:
        focus.append("график динамики по периоду")
    if _wants_only_graph(user_prompt):
        focus.append("только график и процентные цифры, без таблиц и лишних блоков")
    if "пики" in lower or "лучших дней" in lower or "выруч" in lower:
        focus.append("пики лучших дней и заметные всплески")
    if "котик" in lower or "котиков" in lower:
        focus.append("визуальная подача в стиле запроса, но цифры останутся читаемыми")
    if not focus:
        focus.append("визуализация именно по твоему описанию")

    return (
        "Перед генерацией соберу отчёт так:\n\n"
        f"• Период: {_date_label(start_date)} — {_date_label(end_date)}\n"
        f"• Задача: {requested}\n"
        "• В отчёте будут: " + "; ".join(focus) + "\n"
        "• Базовые цифры: поступления, расходы, итог периода и нужные сравнения\n"
        "• Заметки и финансовую цель не добавляю, если ты прямо не попросишь\n"
        "• Регулярные платежи не включаю в отчёт: они остаются только напоминаниями\n\n"
        "Если всё ок — жми «Сформировать». Если хочешь поправить ТЗ, просто напиши следующим сообщением."
    )


async def build_report_prompt(
    user_id: int,
    year: int,
    month: int,
    user_prompt: str | None = None,
) -> str:
    from app.database import fetchall

    requested = _short(user_prompt or "Сделай красивый финансовый отчёт.", 1000)
    start_date, end_date = _date_range_from_prompt(user_prompt, year, month)
    summary = await _period_summary(user_id, start_date, end_date)
    categories = await fetchall(
        """SELECT COALESCE(c.name, 'Без категории'), t.type, SUM(t.amount)
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.transaction_date BETWEEN %s AND %s
           GROUP BY c.name, t.type
           ORDER BY 3 DESC
           LIMIT 80""",
        (user_id, start_date, end_date),
    )
    daily_rows = await fetchall(
        """SELECT transaction_date,
                  COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0),
                  COUNT(*)
           FROM transactions
           WHERE user_id=%s AND transaction_date BETWEEN %s AND %s
           GROUP BY transaction_date
           ORDER BY transaction_date""",
        (user_id, start_date, end_date),
    )
    monthly_rows = await fetchall(
        """SELECT DATE_TRUNC('month', transaction_date)::date,
                  COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0)
           FROM transactions
           WHERE user_id=%s AND transaction_date BETWEEN %s AND %s
           GROUP BY 1
           ORDER BY 1""",
        (user_id, start_date, end_date),
    )
    transactions = await fetchall(
        """SELECT t.transaction_date, t.amount, t.type, COALESCE(c.name, 'Без категории'), t.comment
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.transaction_date BETWEEN %s AND %s
           ORDER BY t.transaction_date DESC, t.created_at DESC
           LIMIT 35""",
        (user_id, start_date, end_date),
    )
    category_daily_rows = await fetchall(
        """SELECT t.transaction_date, COALESCE(c.name, 'Без категории'), t.type, SUM(t.amount)
           FROM transactions t
           LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.user_id=%s AND t.transaction_date BETWEEN %s AND %s
           GROUP BY t.transaction_date, c.name, t.type
           ORDER BY t.transaction_date, c.name""",
        (user_id, start_date, end_date),
    )
    notes = []
    if _wants_notes(user_prompt):
        notes = await fetchall(
            "SELECT text, created_at FROM notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 8",
            (user_id,),
        )

    category_lines = [
        f"- {row[0]} ({'доход' if row[1] == 'income' else 'расход'}): {_rub(row[2])}"
        for row in categories[:16]
    ]
    daily_lines = [
        f"- {_date_label(row[0])}: поступления {_rub(row[1])}, расходы {_rub(row[2])}, операций {row[3]}"
        for row in daily_rows[:45]
    ]
    income_peaks = sorted(daily_rows, key=lambda row: float(row[1] or 0), reverse=True)[:5]
    expense_peaks = sorted(daily_rows, key=lambda row: float(row[2] or 0), reverse=True)[:5]
    income_peak_lines = [
        f"- {_date_label(row[0])}: {_rub(row[1])}" for row in income_peaks if float(row[1] or 0) > 0
    ]
    expense_peak_lines = [
        f"- {_date_label(row[0])}: {_rub(row[2])}" for row in expense_peaks if float(row[2] or 0) > 0
    ]
    monthly_lines = [
        f"- {_month_title(row[0].year, row[0].month)}: поступления {_rub(row[1])}, расходы {_rub(row[2])}"
        for row in monthly_rows
    ]
    transaction_lines = []
    for row in transactions:
        direction = "доход" if row[2] == "income" else "расход"
        comment = f"; комментарий: {_short(row[4], 70)}" if row[4] else ""
        transaction_lines.append(f"- {_date_label(row[0])}: {direction} {_rub(row[1])}, {row[3]}{comment}")
    note_lines = [
        f"- {_date_label(row[1])}: {_short(row[0], 120)}" for row in notes
    ]
    focus_block = _build_focus_block(user_prompt, summary, categories, daily_rows, category_daily_rows)
    only_graph = _wants_only_graph(user_prompt)
    wants_percentages = _wants_percentages(user_prompt)

    strict_layout_rules = []
    if only_graph:
        strict_layout_rules.extend([
            "- СТРОГО: сделай только один график/диаграмму и крупные процентные цифры.",
            "- НЕ добавляй карточки с общим итогом, последние операции, категории, помесячные таблицы, выводы и декоративные блоки.",
            "- Заголовок должен отражать конкретный запрос, а не общий 'Финансовый отчёт'.",
        ])
    if wants_percentages:
        strict_layout_rules.append("- Обязательно покажи проценты крупно: доля выбранного расхода от поступлений и, если уместно, от всех расходов.")
    if focus_block:
        strict_layout_rules.append("- Фокус запроса важнее всех общих данных. Если данные ниже противоречат фокусу, используй фокус.")
    strict_layout_text = "\n".join(strict_layout_rules)

    if only_graph:
        return (
            "Создай финальную визуальную финансовую мини-инфографику для Telegram на русском языке. "
            "Это не общий отчёт, а узкий график по конкретной просьбе пользователя.\n\n"
            "Главная просьба пользователя:\n"
            f"{requested}\n\n"
            "Жёсткие правила макета:\n"
            + strict_layout_text
            + "\n- Не раскрывай внутренние сервисы, API, провайдеров и технические детали.\n"
            "- Не добавляй вымышленные цифры. Используй только данные ниже.\n\n"
            f"Период данных: {_date_label(start_date)} - {_date_label(end_date)}\n"
            f"Итого поступления: {_rub(summary['income'])}\n"
            f"Итого расходы: {_rub(summary['total_expense'])}\n"
            f"Итог периода: {_rub(summary['balance'])}\n\n"
            + focus_block
            + "\nДневная динамика всего периода:\n"
            + _join_lines(daily_lines)
        )

    return (
        "Создай финальный визуальный финансовый отчёт для Telegram на русском языке. "
        "Это должен быть готовый красивый экран/инфографика, а не объяснение процесса. "
        "Стиль по умолчанию: чистый современный fintech-дизайн, светлый фон, зелёные и графитовые акценты, "
        "крупные цифры, аккуратные карточки, читаемые подписи.\n\n"
        "Если пользователь не попросил другой тип графика, главный график сделай в виде финансовых свечей по дням: "
        "зелёные свечи для дней с плюсом, красные для дней с минусом, рядом подпиши поступления, расходы и итог.\n\n"
        "Главная просьба пользователя:\n"
        f"{requested}\n\n"
        "Правила:\n"
        + (strict_layout_text + "\n" if strict_layout_text else "")
        + "- Выполни именно просьбу пользователя: он может просить график, сравнение, один блок, исключение блоков или необычный визуальный стиль.\n"
        "- Регулярные платежи и календарь платежей не показывай в отчёте: они используются только как напоминания.\n"
        "- По умолчанию не используй заметки и финансовые цели. Заметки можно использовать только если пользователь явно попросил.\n"
        "- Не дели расходы на внутренние типы. Все расходы показывай как обычные расходы или по категориям.\n"
        "- Можно использовать метафоры и оформление из просьбы пользователя, например котиков, но цифры должны остаться читаемыми.\n"
        "- Не раскрывай внутренние сервисы, API, провайдеров и технические детали.\n"
        "- Не добавляй вымышленные цифры. Используй только данные ниже.\n"
        "- Если нужного среза данных нет, покажи это мягко: 'данных за период нет' или 'категория не найдена'.\n\n"
        f"Период данных: {_date_label(start_date)} - {_date_label(end_date)}\n"
        f"Итого поступления: {_rub(summary['income'])}\n"
        f"Итого расходы: {_rub(summary['total_expense'])}\n"
        f"Итог периода: {_rub(summary['balance'])}\n\n"
        + (focus_block + "\n" if focus_block else "")
        + "Категории и статьи:\n" + _join_lines(category_lines) + "\n\n"
        "Помесячное сравнение:\n" + _join_lines(monthly_lines) + "\n\n"
        "Дневная динамика:\n" + _join_lines(daily_lines) + "\n\n"
        "Пики поступлений:\n" + _join_lines(income_peak_lines) + "\n\n"
        "Пики расходов:\n" + _join_lines(expense_peak_lines) + "\n\n"
        "Операции за период:\n" + _join_lines(transaction_lines) + "\n\n"
        "Заметки пользователя, если они нужны для запроса:\n" + _join_lines(note_lines) + "\n\n"
        "Добавь короткий вывод на 1-2 строки: спокойный, полезный, без морализаторства."
    )


async def create_kie_image_task(prompt: str) -> str:
    api_key = os.getenv("KIE_API_KEY")
    if not api_key:
        raise RuntimeError("KIE_API_KEY не настроен")

    callback_url = os.getenv("KIE_CALLBACK_URL")
    payload = {
        "model": KIE_IMAGE_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": KIE_REPORT_ASPECT_RATIO,
        },
    }
    if callback_url:
        payload["callBackUrl"] = callback_url

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            KIE_API_BASE + "/api/v1/jobs/createTask",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    if data.get("code") != 200:
        raise RuntimeError(data.get("msg") or "Kie.ai не принял задачу")
    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError("Kie.ai не вернул taskId")
    return task_id


async def get_kie_task(task_id: str) -> dict:
    api_key = os.getenv("KIE_API_KEY")
    if not api_key:
        raise RuntimeError("KIE_API_KEY не настроен")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            KIE_API_BASE + "/api/v1/jobs/recordInfo",
            headers={"Authorization": "Bearer " + api_key},
            params={"taskId": task_id},
        )
        response.raise_for_status()
        data = response.json()

    if data.get("code") not in (200, 505):
        raise RuntimeError(data.get("msg") or "Не удалось получить статус Kie.ai")
    return data.get("data") or {}


def extract_result_url(task_data: dict) -> str | None:
    result_json = task_data.get("resultJson")
    if not result_json:
        return None
    try:
        result = json.loads(result_json)
    except (TypeError, json.JSONDecodeError):
        return None
    for key in ("resultUrls", "urls", "images"):
        value = result.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("imageUrl")
    return result.get("url") or result.get("imageUrl")


async def start_beautiful_report(user_id: int, year: int, month: int, user_prompt: str | None = None) -> dict:
    prompt = await build_report_prompt(user_id, year, month, user_prompt)
    task_id = await create_kie_image_task(prompt)
    return {"task_id": task_id, "prompt": prompt}
