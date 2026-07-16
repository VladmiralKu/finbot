import re

from app.database import fetchall, fetchone


def format_public_transaction_id(month: int, month_index: int) -> str:
    return f"{int(month)}{int(month_index)}"


def _digits(raw: str | int | None) -> str:
    return re.sub(r"\D+", "", str(raw or ""))


def public_number_candidates(raw: str | int | None, month: int | None = None) -> list[tuple[int, int]]:
    digits = _digits(raw)
    if not digits:
        return []

    candidates: list[tuple[int, int]] = []
    if month:
        month_num = int(month)
        prefix = str(month_num)
        if digits.startswith(prefix) and len(digits) > len(prefix):
            ordinal = int(digits[len(prefix):])
            if ordinal > 0:
                candidates.append((month_num, ordinal))
        ordinal = int(digits)
        if ordinal > 0:
            candidates.append((month_num, ordinal))
        return _unique_candidates(candidates)

    if len(digits) >= 2:
        one_digit_month = int(digits[:1])
        one_digit_ordinal = int(digits[1:])
        if 1 <= one_digit_month <= 9 and one_digit_ordinal > 0:
            candidates.append((one_digit_month, one_digit_ordinal))

    if len(digits) >= 3:
        two_digit_month = int(digits[:2])
        two_digit_ordinal = int(digits[2:])
        if 10 <= two_digit_month <= 12 and two_digit_ordinal > 0:
            candidates.append((two_digit_month, two_digit_ordinal))

    return _unique_candidates(candidates)


def _unique_candidates(candidates: list[tuple[int, int]]) -> list[tuple[int, int]]:
    seen = set()
    result = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


async def public_numbers_for_ids(user_id: int, transaction_ids: list[int]) -> dict[int, str]:
    ids = [int(tx_id) for tx_id in transaction_ids if tx_id]
    if not ids:
        return {}

    placeholders = ", ".join(["%s"] * len(ids))
    rows = await fetchall(
        f"""
        WITH ranked AS (
            SELECT
                t.id,
                EXTRACT(MONTH FROM t.transaction_date)::int AS month_num,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        EXTRACT(YEAR FROM t.transaction_date)::int,
                        EXTRACT(MONTH FROM t.transaction_date)::int
                    ORDER BY t.transaction_date ASC, t.created_at ASC, t.id ASC
                )::int AS month_index
            FROM transactions t
            WHERE t.user_id=%s
        )
        SELECT id, month_num, month_index
        FROM ranked
        WHERE id IN ({placeholders})
        """,
        (user_id, *ids),
    )
    return {
        int(row[0]): format_public_transaction_id(row[1], row[2])
        for row in rows
    }


async def public_number_for_transaction(user_id: int, transaction_id: int) -> str:
    numbers = await public_numbers_for_ids(user_id, [transaction_id])
    return numbers.get(int(transaction_id), str(transaction_id))


async def _resolve_public_candidate(
    user_id: int,
    month: int,
    ordinal: int,
    year: int | None = None,
) -> tuple[int, object] | None:
    conditions = ["month_num=%s", "month_index=%s"]
    params: list[object] = [user_id, int(month), int(ordinal)]
    if year:
        conditions.append("year_num=%s")
        params.append(int(year))

    return await fetchone(
        """
        WITH ranked AS (
            SELECT
                t.id,
                t.transaction_date,
                EXTRACT(YEAR FROM t.transaction_date)::int AS year_num,
                EXTRACT(MONTH FROM t.transaction_date)::int AS month_num,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        EXTRACT(YEAR FROM t.transaction_date)::int,
                        EXTRACT(MONTH FROM t.transaction_date)::int
                    ORDER BY t.transaction_date ASC, t.created_at ASC, t.id ASC
                )::int AS month_index
            FROM transactions t
            WHERE t.user_id=%s
        )
        SELECT id, transaction_date
        FROM ranked
        WHERE """ + " AND ".join(conditions) + """
        ORDER BY transaction_date DESC, id DESC
        LIMIT 1
        """,
        tuple(params),
    )


async def resolve_transaction_reference(
    user_id: int,
    raw: str | int | None,
    year: int | None = None,
    month: int | None = None,
) -> int | None:
    digits = _digits(raw)
    if not digits:
        return None

    candidates = public_number_candidates(digits, month=month)
    matches = []
    for candidate_month, ordinal in candidates:
        row = await _resolve_public_candidate(user_id, candidate_month, ordinal, year=year)
        if row:
            if year or month:
                return int(row[0])
            matches.append(row)

    if matches:
        matches.sort(key=lambda item: item[1], reverse=True)
        return int(matches[0][0])

    return None
