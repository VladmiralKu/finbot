import csv
import hashlib
import io
import re
from datetime import date, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database import can_use_feature, import_hash_exists
from app.services.category_matcher import match_category
from app.services.transaction_service import create_transaction

router = Router()


class BankImportState(StatesGroup):
    preview = State()


DATE_COLUMNS = {"date", "дата", "дата операции", "operation date", "posted date"}
AMOUNT_COLUMNS = {"amount", "сумма", "сумма операции"}
DEBIT_COLUMNS = {"debit", "дебет", "списание", "расход"}
CREDIT_COLUMNS = {"credit", "кредит", "поступление", "доход"}
DESC_COLUMNS = {"description", "назначение", "описание", "детали", "merchant", "контрагент"}


def _normalize_header(value: str) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value


def _parse_money(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("\u2212", "-").replace(" ", "").replace("\xa0", "")
    raw = re.sub(r"[^\d,.\-+]", "", raw)
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            pass
    return None


def _pick(mapping: dict, names: set[str]):
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _hash_operation(user_id: int, tx_date: date, amount: float, type_: str, description: str) -> str:
    normalized_description = re.sub(r"\s+", " ", (description or "").lower()).strip()
    raw = f"{user_id}|{tx_date.isoformat()}|{amount:.2f}|{type_}|{normalized_description}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_csv(data: bytes) -> list[dict]:
    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="ignore")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return list(reader)


def _read_xlsx(data: bytes) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(cell or "").strip() for cell in rows[0]]
    result = []
    for row in rows[1:]:
        result.append({headers[i]: row[i] if i < len(row) else None for i in range(len(headers))})
    return result


async def _normalize_rows(user_id: int, rows: list[dict]) -> tuple[list[dict], int, int]:
    prepared = []
    duplicates = 0
    seen_hashes = set()

    for raw_row in rows[:1000]:
        header_map = {_normalize_header(key): value for key, value in raw_row.items()}
        tx_date = _parse_date(_pick(header_map, DATE_COLUMNS))
        description = str(_pick(header_map, DESC_COLUMNS) or "").strip()

        debit = _parse_money(_pick(header_map, DEBIT_COLUMNS))
        credit = _parse_money(_pick(header_map, CREDIT_COLUMNS))
        amount = _parse_money(_pick(header_map, AMOUNT_COLUMNS))

        if debit and debit != 0:
            tx_type = "expense"
            normalized_amount = abs(debit)
        elif credit and credit != 0:
            tx_type = "income"
            normalized_amount = abs(credit)
        elif amount is not None and amount != 0:
            tx_type = "expense" if amount < 0 else "income"
            normalized_amount = abs(amount)
        else:
            continue

        if not tx_date:
            continue

        import_hash = _hash_operation(user_id, tx_date, normalized_amount, tx_type, description)
        if import_hash in seen_hashes or await import_hash_exists(user_id, import_hash):
            duplicates += 1
            continue
        seen_hashes.add(import_hash)

        category = await match_category(
            user_id,
            description,
            type_hint=tx_type,
            amount=normalized_amount,
            source="bank_import",
        )
        if not category:
            continue

        prepared.append({
            "amount": normalized_amount,
            "type": tx_type,
            "category_id": category["category_id"],
            "category_name": category["category_name"],
            "kind": category["kind"],
            "comment": description,
            "transaction_date": tx_date,
            "import_hash": import_hash,
        })

    skipped_by_limit = max(0, len(rows) - 1000)
    return prepared, duplicates, skipped_by_limit


def _preview_text(operations: list[dict], duplicates: int, skipped_by_limit: int) -> str:
    expense = sum(tx["amount"] for tx in operations if tx["type"] == "expense")
    income = sum(tx["amount"] for tx in operations if tx["type"] == "income")
    found = len(operations) + duplicates
    text = (
        f"Нашёл {found} операций.\n"
        f"К импорту: {len(operations)}\n"
        f"Похоже на дубли: {duplicates}\n\n"
        f"Расходы: {expense:,.0f} ₽\n"
        f"Доходы: {income:,.0f} ₽\n"
    )
    if skipped_by_limit:
        text += f"\nФайл длинный, взял первые 1000 строк. Пропущено строк: {skipped_by_limit}\n"

    text += "\nПервые операции:\n"
    for tx in operations[:10]:
        sign = "-" if tx["type"] == "expense" else "+"
        text += (
            f"{tx['transaction_date'].strftime('%d.%m')} "
            f"{sign}{tx['amount']:,.0f} {tx['category_name']} — {tx['comment'][:80]}\n"
        )
    return text


@router.message(F.document)
async def msg_bank_statement(message: Message, state: FSMContext):
    file_name = message.document.file_name or ""
    lower_name = file_name.lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        await message.answer("Пока умею загружать CSV и XLSX. PDF добавим отдельно.")
        return

    feature_limit = await can_use_feature(message.from_user.id, "excel_import")
    if feature_limit == 0 or feature_limit is False:
        await message.answer(
            "Импорт выписок доступен на тарифах с Excel-импортом.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            ]),
        )
        return

    thinking = await message.answer("Читаю выписку...")
    try:
        file = await message.bot.get_file(message.document.file_id)
        downloaded = await message.bot.download_file(file.file_path)
        data = downloaded.read()

        rows = _read_csv(data) if lower_name.endswith(".csv") else _read_xlsx(data)
        operations, duplicates, skipped_by_limit = await _normalize_rows(message.from_user.id, rows)
        await thinking.delete()

        if not operations:
            await message.answer(
                "Не нашёл операций для импорта. Проверь, что в файле есть дата, сумма и описание.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ]),
            )
            return

        await state.set_state(BankImportState.preview)
        await state.update_data(bank_import_operations=operations)
        await message.answer(
            _preview_text(operations, duplicates, skipped_by_limit),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Импортировать", callback_data="bank_import_confirm"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="bank_import_cancel"),
                ],
            ]),
        )
    except Exception as e:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer("Ошибка импорта: " + str(e))


@router.callback_query(F.data == "bank_import_cancel")
async def cb_bank_import_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "Импорт отменён. Транзакции не созданы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]),
    )


@router.callback_query(F.data == "bank_import_confirm")
async def cb_bank_import_confirm(call: CallbackQuery, state: FSMContext):
    from app.services.insights import build_first_transaction_insight

    data = await state.get_data()
    operations = data.get("bank_import_operations") or []
    created = 0
    duplicates = 0
    created_ids = []

    for tx in operations:
        if await import_hash_exists(call.from_user.id, tx["import_hash"]):
            duplicates += 1
            continue
        saved = await create_transaction(
            user_id=call.from_user.id,
            category_id=tx["category_id"],
            amount=tx["amount"],
            type_=tx["type"],
            kind=tx["kind"],
            comment=tx["comment"],
            transaction_date=tx["transaction_date"],
            import_hash=tx["import_hash"],
        )
        created_ids.append(saved["id"])
        created += 1

    await state.clear()
    text = f"Импорт завершён.\nСоздано: {created}\nДубли пропущены: {duplicates}"
    insight = await build_first_transaction_insight(call.from_user.id, created_ids)
    if insight:
        text += "\n\n" + insight
    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]),
    )
