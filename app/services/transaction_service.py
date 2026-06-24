from datetime import date

from app.database import fetchone


async def create_transaction(
    user_id: int,
    amount: float,
    type_: str,
    category_id: int,
    kind: str | None = None,
    comment: str = "",
    transaction_date=None,
    pnl_period: str | None = None,
    receipt_photo_id: str | None = None,
    import_hash: str | None = None,
) -> dict:
    amount = abs(float(amount))
    if type_ not in ("expense", "income"):
        type_ = "expense"

    category = await fetchone(
        "SELECT id, type, kind FROM categories WHERE id=%s AND user_id=%s",
        (category_id, user_id),
    )
    if not category:
        raise ValueError("Категория не найдена")

    category_type = category[1]
    category_kind = category[2]
    type_ = category_type or type_
    if kind is None:
        kind = category_kind
    if type_ == "income":
        kind = "income"
    if transaction_date is None:
        transaction_date = date.today()

    row = await fetchone(
        """INSERT INTO transactions
           (user_id, category_id, amount, type, kind, comment, receipt_photo_id,
            transaction_date, wallet, pnl_period, import_hash)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'cash',%s,%s)
           RETURNING id""",
        (
            user_id,
            category_id,
            amount,
            type_,
            kind,
            comment or "",
            receipt_photo_id,
            transaction_date,
            pnl_period,
            import_hash,
        ),
    )
    return {
        "id": row[0],
        "amount": amount,
        "type": type_,
        "kind": kind,
        "category_id": category_id,
        "transaction_date": transaction_date,
    }
