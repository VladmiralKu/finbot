from datetime import date
from html import escape

from app.services.insights import build_first_transaction_insight, build_transaction_insight
from app.services.onboarding_video import maybe_send_onboarding_video
from app.services.transaction_public_ids import public_number_for_transaction, public_numbers_for_ids
from app.services.transaction_service import create_transaction


def _amount_text(tx: dict) -> str:
    sign = "−" if tx.get("type") == "expense" else "+"
    return sign + f"{float(tx.get('amount') or 0):,.0f}".replace(",", " ") + " ₽"


def _date_value(value):
    if hasattr(value, "strftime"):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return date.today()
    return date.today()


def _comment_html(comment: str | None) -> str:
    value = (comment or "").strip()
    if not value:
        return ""
    return "\n   💬 <i>«" + escape(value) + "»</i>"


async def save_transactions(user_id: int, transactions: list[dict]) -> tuple[list[int], list[tuple[dict, dict]]]:
    saved_items = []
    saved_ids = []
    for tx in transactions:
        tx_date = _date_value(tx.get("transaction_date"))
        saved = await create_transaction(
            user_id=user_id,
            category_id=tx["category_id"],
            amount=tx["amount"],
            type_=tx["type"],
            kind=tx.get("kind"),
            comment=tx.get("comment") or "",
            transaction_date=tx_date,
            pnl_period=tx.get("pnl_period"),
        )
        item = dict(tx)
        item["transaction_date"] = tx_date
        saved_items.append((saved, item))
        saved_ids.append(saved["id"])
    return saved_ids, saved_items


async def build_saved_transactions_response(user_id: int, saved_ids: list[int], saved_items: list[tuple[dict, dict]]) -> str:
    if not saved_items:
        return "Не нашёл операции, которые можно уверенно внести."

    if len(saved_items) == 1:
        saved, tx = saved_items[0]
        public_id = await public_number_for_transaction(user_id, saved["id"])
        tx_date = _date_value(tx.get("transaction_date"))
        text = (
            "✅ <b>Записано!</b>\n\n"
            + "<b>" + escape(_amount_text(tx)) + "</b>\n"
            + "📂 " + escape(str(tx.get("category_name") or "Без категории")) + "\n"
            + "📅 " + escape(tx_date.strftime("%d.%m.%Y")) + "\n"
            + "🔢 #" + escape(str(public_id))
            + _comment_html(tx.get("comment"))
        )
        if tx.get("display_note"):
            text += "\n💱 " + escape(str(tx["display_note"]))
        if tx.get("pnl_period"):
            text += "\n📊 ПнЛ: " + escape(str(tx["pnl_period"]))
        insight = await build_transaction_insight(user_id, saved["id"])
        if insight:
            text += "\n\n" + escape(insight)
        return text

    public_ids = await public_numbers_for_ids(user_id, saved_ids)
    lines = []
    for index, (saved, tx) in enumerate(saved_items, start=1):
        tx_date = _date_value(tx.get("transaction_date"))
        public_id = public_ids.get(int(saved["id"]), str(saved["id"]))
        line = (
            str(index) + ". #" + escape(str(public_id)) + " "
            + escape(tx_date.strftime("%d.%m")) + " "
            + "<b>" + escape(_amount_text(tx)) + "</b>"
            + " — " + escape(str(tx.get("category_name") or "Без категории"))
            + (" " + escape(str(tx["display_note"])) if tx.get("display_note") else "")
            + _comment_html(tx.get("comment"))
        )
        lines.append(line)

    text = "✅ <b>Записал " + str(len(saved_items)) + " операций. Что внёс:</b>\n\n" + "\n".join(lines)
    insight = await build_first_transaction_insight(user_id, saved_ids)
    if insight:
        text += "\n\n" + escape(insight)
    return text


async def save_transactions_and_build_response(user_id: int, transactions: list[dict]) -> tuple[list[int], str]:
    saved_ids, saved_items = await save_transactions(user_id, transactions)
    return saved_ids, await build_saved_transactions_response(user_id, saved_ids, saved_items)


async def send_saved_transactions_response(message, user_id: int, transactions: list[dict], reply_markup):
    saved_ids, text = await save_transactions_and_build_response(user_id, transactions)
    await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    if saved_ids:
        await maybe_send_onboarding_video(message.bot, user_id)
    return saved_ids
