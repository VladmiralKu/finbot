import difflib
import re

from app.database import execute, fetchall, fetchone, get_categories
from app.services.category_matcher import normalize_name


def normalize_category_display_name(value: str) -> str:
    value = re.sub(r"^[\"'«]+|[\"'»]+$", "", (value or "").strip())
    value = re.sub(r"[.!?…]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return " ".join(word[:1].upper() + word[1:] for word in value.split(" "))


def _clean_name(value: str) -> str:
    return normalize_category_display_name(value)


def _strip_category_word(value: str) -> str:
    value = re.sub(r"^\s*категори[яюи]\s+", "", value, flags=re.IGNORECASE)
    return _clean_name(value)


def _looks_like_category_command(text: str) -> bool:
    lower = text.lower()
    markers = (
        "категор", "переимен", "замени", "заменить", "запени", "помен",
        "смени", "сменить", "добав", "удали",
        "постоянн", "переменн",
    )
    return any(marker in lower for marker in markers)


async def _find_category(user_id: int, name: str, scope_type: str) -> dict | None:
    target = normalize_name(name)
    categories = await get_categories(user_id, type_=scope_type)
    best = None
    best_score = 0.0
    for cat in categories:
        if normalize_name(cat["name"]) == target:
            return cat
    for cat in categories:
        cat_norm = normalize_name(cat["name"])
        if target and (target in cat_norm or cat_norm in target):
            return cat
        for target_token in target.split():
            for cat_token in cat_norm.split():
                score = difflib.SequenceMatcher(None, target_token, cat_token).ratio()
                if score > best_score:
                    best = cat
                    best_score = score
    if best and best_score >= 0.72:
        return best
    return None


async def parse_category_command(user_id: int, text: str, scope_type: str) -> dict | None:
    if scope_type not in ("expense", "income"):
        return None
    if not text or not _looks_like_category_command(text):
        return None

    source = text.strip()
    source = re.sub(r"\bзапени\b", "замени", source, flags=re.IGNORECASE)
    lower = source.lower()

    kind = None
    if "постоянн" in lower:
        kind = "fixed"
    elif "переменн" in lower:
        kind = "variable"

    if kind:
        name = source
        for pattern in (
            r"сделай\s+(.+?)\s+постоянн",
            r"сделать\s+(.+?)\s+постоянн",
            r"(.+?)\s+теперь\s+переменн",
            r"(.+?)\s+это\s+переменн",
            r"(.+?)\s+это\s+постоянн",
            r"(.+?)\s+теперь\s+постоянн",
        ):
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                name = match.group(1)
                break
        return {
            "intent": "set_category_kind",
            "category_name": _strip_category_word(name),
            "kind": kind,
            "confidence": 0.9,
        }

    match = re.search(
        r"(?:замени(?:ть)?|поменяй|поменять|смени|сменить|переименуй|переименовать)\s+(?:категори[яю]\s+)?(.+?)\s+(?:на|в)\s+(.+)$",
        source,
        flags=re.IGNORECASE,
    )
    if match:
        return {
            "intent": "rename_category",
            "old_name": _strip_category_word(match.group(1)),
            "new_name": _strip_category_word(match.group(2)),
            "confidence": 0.92,
        }

    match = re.search(
        r"(?:категори[яю]|стать[яю]|раздел)\s+(.+?)\s+(?:замени(?:ть)?|поменяй|поменять|смени|сменить|переименуй|переименовать)\s+(?:на|в)\s+(.+)$",
        source,
        flags=re.IGNORECASE,
    )
    if match:
        return {
            "intent": "rename_category",
            "old_name": _strip_category_word(match.group(1)),
            "new_name": _strip_category_word(match.group(2)),
            "confidence": 0.9,
        }

    match = re.search(r"(?:добавь|добавить)\s+(?:категори[яю]\s+)?(.+)$", source, flags=re.IGNORECASE)
    if match:
        return {
            "intent": "add_category",
            "name": _strip_category_word(match.group(1)),
            "confidence": 0.9,
        }

    match = re.search(r"(?:удали|удалить)\s+(?:категори[яю]\s+)?(.+)$", source, flags=re.IGNORECASE)
    if match:
        return {
            "intent": "delete_category",
            "name": _strip_category_word(match.group(1)),
            "confidence": 0.9,
        }

    return {"intent": "unknown", "confidence": 0.0}


async def apply_category_command(user_id: int, command: dict, scope_type: str) -> str:
    intent = (command or {}).get("intent")
    if scope_type not in ("expense", "income"):
        return "Не понял, к каким категориям применить команду."

    if intent == "rename_category":
        old_name = _clean_name(command.get("old_name", ""))
        new_name = _clean_name(command.get("new_name", ""))
        if not old_name or not new_name:
            return "Не понял, какую категорию переименовать."

        existing = await _find_category(user_id, old_name, scope_type)
        if not existing:
            return f"Категория «{old_name}» не найдена."

        duplicate = await _find_category(user_id, new_name, scope_type)
        if duplicate and duplicate["id"] != existing["id"]:
            return f"Категория «{new_name}» уже есть."

        await execute(
            "UPDATE categories SET name=%s WHERE id=%s AND user_id=%s AND type=%s",
            (new_name, existing["id"], user_id, scope_type),
        )
        return f"Готово: «{existing['name']}» теперь «{new_name}»."

    if intent == "add_category":
        name = _clean_name(command.get("name", ""))
        if not name:
            return "Не понял название новой категории."
        duplicate = await _find_category(user_id, name, scope_type)
        if duplicate:
            return f"Категория «{duplicate['name']}» уже есть."
        kind = "income" if scope_type == "income" else "variable"
        await execute(
            "INSERT INTO categories (user_id, name, type, kind) VALUES (%s, %s, %s, %s)",
            (user_id, name, scope_type, kind),
        )
        return f"Готово: добавил категорию «{name}»."

    if intent == "delete_category":
        name = _clean_name(command.get("name", ""))
        existing = await _find_category(user_id, name, scope_type)
        if not existing:
            return f"Категория «{name}» не найдена."
        used = await fetchone(
            "SELECT COUNT(*) FROM transactions WHERE user_id=%s AND category_id=%s",
            (user_id, existing["id"]),
        )
        if used and int(used[0]) > 0:
            return "Категория уже используется в транзакциях. Пока можно только переименовать."
        await execute(
            "DELETE FROM categories WHERE id=%s AND user_id=%s AND type=%s",
            (existing["id"], user_id, scope_type),
        )
        return f"Готово: удалил категорию «{existing['name']}»."

    if intent == "set_category_kind":
        if scope_type == "income":
            return "Доходные категории не бывают постоянными или переменными расходами."
        name = _clean_name(command.get("category_name", ""))
        existing = await _find_category(user_id, name, scope_type)
        if not existing:
            return f"Категория «{name}» не найдена."
        kind = command.get("kind")
        if kind not in ("fixed", "variable"):
            return "Не понял, сделать категорию постоянной или переменной."
        await execute(
            "UPDATE categories SET kind=%s WHERE user_id=%s AND id=%s AND type='expense'",
            (kind, user_id, existing["id"]),
        )
        label = "постоянный расход" if kind == "fixed" else "переменный расход"
        return f"Готово: «{existing['name']}» теперь {label}."

    return "Не понял команду. Можно выбрать категорию кнопкой или написать: «замени категорию X на Y»."
