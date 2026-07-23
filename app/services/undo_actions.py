import json
import re
from html import escape

from app.database import execute, fetchall, fetchone


ENTITY_TABLES = {
    "transaction": "transactions",
    "note": "notes",
    "recurring_payment": "recurring_payments",
    "user_goal": "user_goals",
    "credit_card": "credit_cards",
}

ENTITY_TITLES = {
    "transaction": "операцию",
    "note": "заметку",
    "recurring_payment": "платёж в календаре",
    "user_goal": "финансовую цель",
    "credit_card": "кредитку",
}

ACTION_TITLES = {
    "insert": "добавление",
    "update": "изменение",
    "delete": "удаление",
}


def _as_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value)


def _short(value, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _rub(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"{amount:,.0f}".replace(",", " ") + " ₽"


def _date_text(value) -> str:
    if not value:
        return "без даты"
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    text = str(value)
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return f"{match.group(3)}.{match.group(2)}.{match.group(1)}"
    return text


def _quote_identifier(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name or ""):
        raise ValueError("Unsafe database identifier")
    return '"' + name + '"'


async def get_last_undo_action(user_id: int):
    row = await fetchone(
        """SELECT id, entity_type, entity_id, action_type,
                  before_data, after_data, created_at
           FROM undo_actions
           WHERE user_id=%s AND undone_at IS NULL
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (user_id,),
    )
    if not row:
        return None
    return {
        "id": int(row[0]),
        "entity_type": row[1],
        "entity_id": int(row[2]),
        "action_type": row[3],
        "before_data": _as_dict(row[4]),
        "after_data": _as_dict(row[5]),
        "created_at": row[6],
    }


async def _category_name(user_id: int, category_id) -> str:
    if not category_id:
        return "без категории"
    row = await fetchone(
        "SELECT name FROM categories WHERE id=%s AND user_id=%s",
        (category_id, user_id),
    )
    return str(row[0]) if row else "категория #" + str(category_id)


async def _format_transaction(user_id: int, data: dict) -> str:
    category = await _category_name(user_id, data.get("category_id"))
    sign = "+" if data.get("type") == "income" else "-"
    comment = _short(data.get("comment"), 90)
    comment_part = f" | «{comment}»" if comment else ""
    return (
        f"{_date_text(data.get('transaction_date'))}: "
        f"{sign}{_rub(data.get('amount'))} — {category}{comment_part}"
    )


def _format_note(data: dict) -> str:
    return "«" + (_short(data.get("text"), 180) or "пустая заметка") + "»"


def _format_goal(data: dict) -> str:
    return "«" + (_short(data.get("goal_text"), 180) or "без текста") + "»"


def _format_recurring(data: dict) -> str:
    name = _short(data.get("name"), 80) or "платёж"
    amount = _rub(data.get("amount"))
    repeat_type = data.get("repeat_type") or "повтор"
    if repeat_type == "monthly":
        repeat = "каждый месяц"
        if data.get("repeat_day_of_month"):
            repeat += f" {data.get('repeat_day_of_month')} числа"
    elif repeat_type == "weekly":
        repeat = "каждую неделю"
    elif repeat_type == "daily":
        repeat = "каждый день"
    else:
        repeat = str(repeat_type)
    return f"{name} — {amount}, {repeat}"


def _format_credit_card(data: dict) -> str:
    name = _short(data.get("name"), 80) or "Кредитка"
    details = [f"долг {_rub(data.get('debt_amount'))}"]
    if data.get("credit_limit") is not None:
        details.append(f"лимит {_rub(data.get('credit_limit'))}")
    if data.get("min_payment") is not None:
        details.append(f"мин. платёж {_rub(data.get('min_payment'))}")
    if data.get("payment_day") is not None:
        details.append(f"платёж {data.get('payment_day')} числа")
    return f"{name}: " + ", ".join(details)


async def _format_entity(user_id: int, entity_type: str, data: dict) -> str:
    if entity_type == "transaction":
        return await _format_transaction(user_id, data)
    if entity_type == "note":
        return _format_note(data)
    if entity_type == "recurring_payment":
        return _format_recurring(data)
    if entity_type == "user_goal":
        return _format_goal(data)
    if entity_type == "credit_card":
        return _format_credit_card(data)
    return _short(data, 180)


async def format_undo_prompt(user_id: int, action: dict) -> str:
    entity_type = action["entity_type"]
    action_type = action["action_type"]
    title = ENTITY_TITLES.get(entity_type, "запись")
    action_title = ACTION_TITLES.get(action_type, action_type)

    text = f"↩️ <b>CTRL+Z</b>\n\nОтменить последнее действие?\n\n"
    text += f"<b>Что было:</b> {escape(action_title)} — {escape(title)}.\n"

    if action_type == "update":
        before_text = await _format_entity(user_id, entity_type, action["before_data"])
        after_text = await _format_entity(user_id, entity_type, action["after_data"])
        text += "\n<b>Было:</b>\n" + escape(before_text)
        text += "\n\n<b>Стало:</b>\n" + escape(after_text)
    else:
        data = action["after_data"] if action_type == "insert" else action["before_data"]
        text += "\n" + escape(await _format_entity(user_id, entity_type, data))

    text += "\n\nНажмёшь «Отменить» — верну как было."
    return text


async def _table_columns(table: str) -> list[str]:
    rows = await fetchall(
        """SELECT column_name
           FROM information_schema.columns
           WHERE table_schema='public' AND table_name=%s
           ORDER BY ordinal_position""",
        (table,),
    )
    return [str(row[0]) for row in rows]


async def _set_undo_disabled(enabled: bool):
    await execute(
        "SELECT set_config('app.undo_disabled', %s, false)",
        ("1" if enabled else "0",),
    )


async def _delete_entity(table: str, entity_id: int, user_id: int):
    return await execute(
        f"DELETE FROM {_quote_identifier(table)} WHERE id=%s AND user_id=%s",
        (entity_id, user_id),
    )


async def _insert_entity(table: str, data: dict):
    columns = [col for col in await _table_columns(table) if col in data]
    if not columns:
        return 0
    quoted_columns = ", ".join(_quote_identifier(col) for col in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = [data.get(col) for col in columns]
    return await execute(
        f"INSERT INTO {_quote_identifier(table)} ({quoted_columns}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
        tuple(values),
    )


async def _update_entity(table: str, entity_id: int, user_id: int, data: dict):
    columns = [
        col for col in await _table_columns(table)
        if col in data and col not in ("id", "user_id")
    ]
    if not columns:
        return 0
    set_clause = ", ".join(f"{_quote_identifier(col)}=%s" for col in columns)
    values = [data.get(col) for col in columns]
    values.extend([entity_id, user_id])
    return await execute(
        f"UPDATE {_quote_identifier(table)} SET {set_clause} WHERE id=%s AND user_id=%s",
        tuple(values),
    )


async def undo_action(user_id: int, action_id: int) -> tuple[bool, str]:
    latest = await get_last_undo_action(user_id)
    if not latest or latest["id"] != int(action_id):
        return False, "Появилось новое действие. Нажми CTRL+Z ещё раз, чтобы точно не отменить не то."

    entity_type = latest["entity_type"]
    table = ENTITY_TABLES.get(entity_type)
    if not table:
        return False, "Это действие я пока не умею откатывать автоматически."

    action_type = latest["action_type"]
    before_data = latest["before_data"]
    after_data = latest["after_data"]
    entity_id = latest["entity_id"]

    try:
        await _set_undo_disabled(True)
        if action_type == "insert":
            changed = await _delete_entity(table, entity_id, user_id)
        elif action_type == "delete":
            changed = await _insert_entity(table, before_data)
        elif action_type == "update":
            changed = await _update_entity(table, entity_id, user_id, before_data)
        else:
            changed = 0
    finally:
        await _set_undo_disabled(False)

    if not changed:
        return False, "Не смог откатить: запись уже изменилась или пропала."

    await execute(
        "UPDATE undo_actions SET undone_at=NOW() WHERE id=%s AND user_id=%s",
        (action_id, user_id),
    )
    title = ENTITY_TITLES.get(entity_type, "запись")
    return True, f"↩️ Готово. Откатил последнее действие: {ACTION_TITLES.get(action_type, action_type)} — {title}."
