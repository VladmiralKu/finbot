from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.credit_cards import (
    add_credit_topup,
    create_credit_card,
    credit_month_summary,
    delete_credit_card,
    get_credit_card,
    list_credit_cards,
    parse_balance_payload,
    parse_credit_card_edit_payload,
    parse_credit_card_payload,
    parse_limit_amount,
    parse_topup_amount,
    rub,
    update_credit_card_details,
    update_credit_balance,
    update_credit_limit,
    update_last_credit_topup,
)


router = Router()

CREDIT_FREEFORM_RE = (
    r"(?i)("
    r"остаток.{0,40}(?:кредит|кредитк|долг)|"
    r"(?:кредит|кредитк|долг).{0,40}остаток|"
    r"задолженность|"
    r"лимит|"
    r"(?:пополнил|пополнение|вн[её]с|закинул|погасил).{0,40}(?:карт|кредит)"
    r")"
)


class CreditState(StatesGroup):
    waiting_new_card = State()
    waiting_topup = State()
    waiting_balance = State()
    waiting_limit = State()
    waiting_edit = State()


def _cancel_text(text: str | None) -> bool:
    return (text or "").lower().strip() in {"отмена", "назад", "стоп", "меню"}


def _card_title(card) -> str:
    return str(card[1] or "Кредитка")


def _find_card_from_text(cards, text: str):
    lower = (text or "").lower()
    if len(cards) == 1:
        return cards[0]
    for card in cards:
        name = _card_title(card).lower()
        if name and name in lower:
            return card
        for token in name.replace("-", " ").split():
            if len(token) >= 4 and token in lower:
                return card
    return None


def _progress_line(card) -> str:
    debt = float(card[2] or 0)
    limit = float(card[3] or 0)
    if limit <= 0:
        return "Лимит не указан"
    available = max(limit - debt, 0)
    used_pct = debt / limit * 100 if limit else 0
    return "Доступно: <b>" + rub(available) + "</b> из " + rub(limit) + f" · занято {used_pct:.0f}%"


def _edit_examples_text(card) -> str:
    return (
        "Что меняем в <b>" + escape(_card_title(card)) + "</b>?\n\n"
        "Пиши свободно, можно несколько полей сразу:\n"
        "<code>долг 83500, лимит 150000</code>\n"
        "<code>минимальный платёж 8000, платёж 25 числа</code>\n"
        "<code>название Альфа кредитка</code>\n"
        "<code>ставка 29.9%</code>\n\n"
        "Если ошибся в последнем пополнении:\n"
        "<code>последнее пополнение 5000</code>\n\n"
        "Если ошибся в пополнении, проще поставить правильный текущий долг: "
        "<code>остаток долга 83500</code>."
    )


def _changed_fields_text(updates: dict) -> str:
    labels = {
        "name": "название",
        "debt_amount": "остаток долга",
        "credit_limit": "лимит",
        "min_payment": "минимальный платёж",
        "payment_day": "день платежа",
        "interest_rate": "ставка",
    }
    lines = []
    for key, value in updates.items():
        if key in {"debt_amount", "credit_limit", "min_payment"}:
            shown = rub(value)
        elif key == "payment_day":
            shown = str(value) + " число"
        elif key == "interest_rate":
            shown = str(float(value)).rstrip("0").rstrip(".") + "%"
        else:
            shown = escape(str(value))
        lines.append("• " + labels.get(key, key) + ": <b>" + shown + "</b>")
    return "\n".join(lines)


def _instructions_text() -> str:
    return (
        "💳 <b>Как вести кредитку</b>\n\n"
        "<blockquote>Цель раздела — показать, гасится долг или карта снова подъедает деньги.</blockquote>\n\n"
        "<b>1. Добавить кредитку</b>\n"
        "Пиши свободно:\n"
        "<code>Тинькофф, долг 120000, лимит 150000, платёж 25 числа, минимальный 8000</code>\n\n"
        "<b>2. Пополнение карты</b>\n"
        "Это не обычный расход. Это платёж в кредитку:\n"
        "<code>внёс 10000 с зарплаты</code>\n\n"
        "<b>3. Остаток по кредиту</b>\n"
        "На 1 число или когда удобно:\n"
        "<code>остаток долга 83500, лимит 150000</code>\n\n"
        "<b>4. Увеличение лимита</b>\n"
        "Если банк поднял лимит:\n"
        "<code>лимит увеличили до 200000</code>\n\n"
        "<b>5. Исправить данные</b>\n"
        "Открой кредитку → «Изменить данные» и напиши:\n"
        "<code>долг 83500, минимальный платёж 8000, платёж 25 числа</code>\n\n"
        "<b>6. Удалить кредитку</b>\n"
        "Открой кредитку → «Удалить кредитку». Я сначала спрошу подтверждение.\n\n"
        "В месячном итоге я сравниваю: долг на начало, пополнения и текущий остаток. "
        "Так видно, сколько денег снова ушло с кредитки."
    )


def _credits_menu_kb(cards) -> InlineKeyboardMarkup:
    buttons = []
    for card in cards:
        buttons.append([InlineKeyboardButton(text="💳 " + _card_title(card), callback_data=f"credit_view:{card[0]}")])

    if len(cards) == 1:
        card_id = cards[0][0]
        buttons.append([InlineKeyboardButton(text="➕ Пополнение карты", callback_data=f"credit_topup:{card_id}")])
        buttons.append([InlineKeyboardButton(text="📍 Обновить остаток", callback_data=f"credit_balance:{card_id}")])
    elif len(cards) > 1:
        buttons.append([InlineKeyboardButton(text="➕ Пополнение карты", callback_data="credit_topup_menu")])
        buttons.append([InlineKeyboardButton(text="📍 Обновить остаток", callback_data="credit_balance_menu")])

    buttons.append([InlineKeyboardButton(text="➕ Добавить кредитку", callback_data="credit_add")])
    buttons.append([InlineKeyboardButton(text="Как вносить", callback_data="credit_help")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="reports_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _card_kb(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Пополнение карты", callback_data=f"credit_topup:{card_id}")],
        [InlineKeyboardButton(text="📍 Обновить остаток", callback_data=f"credit_balance:{card_id}")],
        [InlineKeyboardButton(text="⬆️ Увеличить лимит", callback_data=f"credit_limit:{card_id}")],
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data=f"credit_edit:{card_id}")],
        [InlineKeyboardButton(text="🗑 Удалить кредитку", callback_data=f"credit_delete:{card_id}")],
        [InlineKeyboardButton(text="Как вносить", callback_data="credit_help")],
        [InlineKeyboardButton(text="Назад", callback_data="credits_menu")],
    ])


def _choose_card_kb(cards, prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💳 " + _card_title(card), callback_data=f"{prefix}:{card[0]}")]
        for card in cards
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="credits_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _overview_text(user_id: int, cards) -> str:
    if not cards:
        return (
            "💳 <b>Кредиты</b>\n\n"
            "<blockquote>Этот раздел помогает закрывать кредитку, а не просто кормить проценты.</blockquote>\n\n"
            "Сначала добавь карту свободным текстом. Например:\n"
            "<code>Тинькофф, долг 120000, лимит 150000, платёж 25 числа, минимальный 8000</code>\n\n"
            "Дальше появятся кнопки: пополнение карты, обновление остатка и увеличение лимита."
        )

    lines = [
        "💳 <b>Кредиты</b>",
        "",
        "<blockquote>Пополнение кредитки учитывается отдельно от обычных расходов. Так видно реальное закрытие долга.</blockquote>",
        "",
    ]
    for card in cards:
        summary = await credit_month_summary(user_id, card[0])
        lines.append("<b>" + escape(_card_title(card)) + "</b>")
        lines.append("Остаток долга: <b>" + rub(card[2]) + "</b>")
        lines.append(_progress_line(card))
        if summary:
            lines.append(
                "За месяц: пополнено <b>" + rub(summary["topups"]) + "</b>, "
                "долг изменился на <b>" + rub(summary["debt_delta"]) + "</b>"
            )
            if summary["new_card_spending"] > 0:
                lines.append("Похоже, с карты снова потрачено: <b>" + rub(summary["new_card_spending"]) + "</b>")
        lines.append("")

    lines.append("1 числа я сам спрошу: какой сейчас остаток по кредиту.")
    return "\n".join(lines)


async def _card_text(user_id: int, card) -> str:
    summary = await credit_month_summary(user_id, card[0])
    lines = [
        "💳 <b>" + escape(_card_title(card)) + "</b>",
        "",
        "Остаток долга: <b>" + rub(card[2]) + "</b>",
        _progress_line(card),
    ]
    if card[4]:
        lines.append("Минимальный платёж: <b>" + rub(card[4]) + "</b>")
    if card[5]:
        lines.append("День платежа: <b>" + str(card[5]) + " число</b>")
    if card[6]:
        lines.append("Ставка: <b>" + str(float(card[6])).rstrip("0").rstrip(".") + "%</b>")

    if summary:
        lines.extend([
            "",
            "<b>Итог месяца</b>",
            "Долг на начало: " + rub(summary["start_debt"]),
            "Пополнено: " + rub(summary["topups"]),
            "Текущий долг: " + rub(summary["end_debt"]),
            "Долг уменьшился на: " + rub(summary["debt_delta"]),
        ])
        if summary["new_card_spending"] > 0:
            lines.append("Новые траты/проценты по карте: " + rub(summary["new_card_spending"]))
        elif summary["new_card_spending"] < 0:
            lines.append("Погашение идёт быстрее новых трат на: " + rub(abs(summary["new_card_spending"])))

    lines.extend([
        "",
        "<b>Что писать</b>",
        "Пополнение: <code>внёс 10000 с зарплаты</code>",
        "Остаток: <code>остаток долга 83500</code>",
        "Лимит: <code>лимит увеличили до 200000</code>",
    ])
    return "\n".join(lines)


async def _show_credits(target, user_id: int):
    cards = await list_credit_cards(user_id)
    text = await _overview_text(user_id, cards)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=_credits_menu_kb(cards))
        await target.answer()
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=_credits_menu_kb(cards))


async def _send_credit_edit_entry(message: Message, state: FSMContext):
    cards = await list_credit_cards(message.from_user.id)
    if not cards:
        await message.answer(
            "Кредиток пока нет. Сначала добавь её в Отчёты → Кредиты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Открыть кредиты", callback_data="credits_menu")],
            ]),
        )
        return
    if len(cards) > 1:
        await message.answer(
            "Какую кредитку меняем?",
            reply_markup=_choose_card_kb(cards, "credit_edit"),
        )
        return

    card = cards[0]
    await state.set_state(CreditState.waiting_edit)
    await state.update_data(credit_card_id=card[0])
    await message.answer(
        _edit_examples_text(card),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card[0]}")],
        ]),
    )


async def _send_credit_delete_entry(message: Message):
    cards = await list_credit_cards(message.from_user.id)
    if not cards:
        await message.answer(
            "Удалять пока нечего: кредиток нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Открыть кредиты", callback_data="credits_menu")],
            ]),
        )
        return
    if len(cards) > 1:
        await message.answer(
            "Какую кредитку удалить?",
            reply_markup=_choose_card_kb(cards, "credit_delete"),
        )
        return

    card = cards[0]
    await message.answer(
        "Удалить кредитку <b>" + escape(_card_title(card)) + "</b>?\n\n"
        "История останется в базе, но карта пропадёт из раздела и напоминаний.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data=f"credit_delete_confirm:{card[0]}")],
            [InlineKeyboardButton(text="Нет, назад", callback_data=f"credit_view:{card[0]}")],
        ]),
    )


@router.message(Command("credit_edit"), StateFilter(default_state))
async def cmd_credit_edit(message: Message, state: FSMContext):
    await _send_credit_edit_entry(message, state)


@router.message(Command("credit_delete"), StateFilter(default_state))
async def cmd_credit_delete(message: Message):
    await _send_credit_delete_entry(message)


@router.message(F.text.regexp(r"(?i)^(?:исправь|исправить|измени|изменить).{0,35}(?:кредит|кредитк|карту)"), StateFilter(default_state))
async def msg_credit_edit_entry(message: Message, state: FSMContext):
    await _send_credit_edit_entry(message, state)


@router.message(F.text.regexp(r"(?i)^(?:удали|удалить).{0,35}(?:кредит|кредитк|карту)"), StateFilter(default_state))
async def msg_credit_delete_entry(message: Message):
    await _send_credit_delete_entry(message)


@router.callback_query(F.data == "credits_menu")
async def cb_credits_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_credits(call, call.from_user.id)


@router.callback_query(F.data == "credit_help")
async def cb_credit_help(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        _instructions_text(),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="credits_menu")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data == "credit_add")
async def cb_credit_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(CreditState.waiting_new_card)
    await call.message.edit_text(
        "Добавим кредитку. Пиши свободно одной фразой:\n\n"
        "<code>Тинькофф, долг 120000, лимит 150000, платёж 25 числа, минимальный 8000</code>\n\n"
        "Можно без всего лишнего, главное — текущий долг. Остальное потом дополним.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="credits_menu")],
        ]),
    )
    await call.answer()


@router.message(CreditState.waiting_new_card)
async def msg_credit_new_card(message: Message, state: FSMContext):
    if _cancel_text(message.text):
        await state.clear()
        await _show_credits(message, message.from_user.id)
        return

    payload = await parse_credit_card_payload(message.text or "")
    if payload.get("debt_amount") is None:
        await message.answer(
            "Не увидел текущий долг. Напиши примерно так:\n"
            "<code>Тинькофф, долг 120000, лимит 150000</code>",
            parse_mode="HTML",
        )
        return

    card = await create_credit_card(message.from_user.id, payload)
    await state.clear()
    await message.answer(
        "Кредитка добавлена.\n\n" + await _card_text(message.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card[0]),
    )


@router.callback_query(F.data.startswith("credit_view:"))
async def cb_credit_view(call: CallbackQuery, state: FSMContext):
    await state.clear()
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await call.message.edit_text(
        await _card_text(call.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("credit_edit:"))
async def cb_credit_edit(call: CallbackQuery, state: FSMContext):
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await state.set_state(CreditState.waiting_edit)
    await state.update_data(credit_card_id=card_id)
    await call.message.edit_text(
        _edit_examples_text(card),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card_id}")],
        ]),
    )
    await call.answer()


@router.message(CreditState.waiting_edit)
async def msg_credit_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    card_id = int(data.get("credit_card_id"))

    if _cancel_text(message.text):
        await state.clear()
        card = await get_credit_card(message.from_user.id, card_id)
        if card:
            await message.answer(await _card_text(message.from_user.id, card), parse_mode="HTML", reply_markup=_card_kb(card_id))
        else:
            await _show_credits(message, message.from_user.id)
        return

    text = message.text or ""
    lower = text.lower()
    if any(word in lower for word in ("пополнение", "пополнил", "внес", "внёс", "закинул", "погасил")):
        amount = parse_topup_amount(text)
        if amount is None:
            await message.answer("Не увидел сумму пополнения. Например: <code>последнее пополнение 5000</code>", parse_mode="HTML")
            return
        result = await update_last_credit_topup(message.from_user.id, card_id, amount, text)
        await state.clear()
        if result and result.get("error") == "not_latest":
            await message.answer(
                "После последнего пополнения уже были другие изменения по карте. "
                "Чтобы не спутать историю, поставь правильный текущий остаток: "
                "<code>остаток долга 83500</code>",
                parse_mode="HTML",
            )
            return
        if not result:
            await message.answer(
                "Не нашёл пополнений по этой кредитке. Можно просто поставить правильный остаток: "
                "<code>остаток долга 83500</code>",
                parse_mode="HTML",
            )
            return
        card = result["card"]
        await message.answer(
            "Исправил последнее пополнение:\n"
            "было <b>" + rub(result["old_amount"]) + "</b>, стало <b>" + rub(result["new_amount"]) + "</b>\n\n"
            + await _card_text(message.from_user.id, card),
            parse_mode="HTML",
            reply_markup=_card_kb(card_id),
        )
        return

    updates = parse_credit_card_edit_payload(text)
    if not updates:
        card = await get_credit_card(message.from_user.id, card_id)
        if not card:
            await state.clear()
            await message.answer("Карта не найдена.")
            return
        await message.answer(
            "Не понял, что менять.\n\n" + _edit_examples_text(card),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card_id}")],
            ]),
        )
        return

    card = await update_credit_card_details(message.from_user.id, card_id, updates, message.text or "")
    await state.clear()
    if not card:
        await message.answer("Карта не найдена.")
        return
    await message.answer(
        "Изменил:\n" + _changed_fields_text(updates) + "\n\n" + await _card_text(message.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card_id),
    )


@router.callback_query(F.data.startswith("credit_delete:"))
async def cb_credit_delete(call: CallbackQuery, state: FSMContext):
    await state.clear()
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await call.message.edit_text(
        "Удалить кредитку <b>" + escape(_card_title(card)) + "</b>?\n\n"
        "История останется в базе, но карта пропадёт из раздела и напоминаний.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data=f"credit_delete_confirm:{card_id}")],
            [InlineKeyboardButton(text="Нет, назад", callback_data=f"credit_view:{card_id}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("credit_delete_confirm:"))
async def cb_credit_delete_confirm(call: CallbackQuery, state: FSMContext):
    await state.clear()
    card_id = int(call.data.split(":")[1])
    deleted = await delete_credit_card(call.from_user.id, card_id)
    if not deleted:
        await call.answer("Карта не найдена", show_alert=True)
        return
    cards = await list_credit_cards(call.from_user.id)
    await call.message.edit_text(
        "Кредитка <b>" + escape(str(deleted[1] or "Кредитка")) + "</b> удалена.",
        parse_mode="HTML",
        reply_markup=_credits_menu_kb(cards),
    )
    await call.answer()


@router.callback_query(F.data == "credit_topup_menu")
async def cb_credit_topup_menu(call: CallbackQuery):
    cards = await list_credit_cards(call.from_user.id)
    if not cards:
        await call.answer("Сначала добавь кредитку", show_alert=True)
        return
    await call.message.edit_text(
        "Выбери карту, которую пополнил:",
        reply_markup=_choose_card_kb(cards, "credit_topup"),
    )
    await call.answer()


@router.callback_query(F.data == "credit_balance_menu")
async def cb_credit_balance_menu(call: CallbackQuery):
    cards = await list_credit_cards(call.from_user.id)
    if not cards:
        await call.answer("Сначала добавь кредитку", show_alert=True)
        return
    await call.message.edit_text(
        "По какой карте обновляем остаток?",
        reply_markup=_choose_card_kb(cards, "credit_balance"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("credit_topup:"))
async def cb_credit_topup(call: CallbackQuery, state: FSMContext):
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await state.set_state(CreditState.waiting_topup)
    await state.update_data(credit_card_id=card_id)
    await call.message.edit_text(
        "Сколько пополнил по карте <b>" + escape(_card_title(card)) + "</b>?\n\n"
        "Пиши свободно:\n"
        "<code>внёс 10000 с зарплаты</code>\n\n"
        "Это уменьшит долг в разделе кредитов и не попадёт в обычные расходы.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card_id}")],
        ]),
    )
    await call.answer()


@router.message(CreditState.waiting_topup)
async def msg_credit_topup(message: Message, state: FSMContext):
    if _cancel_text(message.text):
        data = await state.get_data()
        await state.clear()
        card = await get_credit_card(message.from_user.id, data.get("credit_card_id"))
        if card:
            await message.answer(await _card_text(message.from_user.id, card), parse_mode="HTML", reply_markup=_card_kb(card[0]))
        else:
            await _show_credits(message, message.from_user.id)
        return

    data = await state.get_data()
    card_id = int(data.get("credit_card_id"))
    amount = parse_topup_amount(message.text or "")
    if not amount:
        await message.answer("Не увидел сумму. Например: <code>внёс 10000</code>", parse_mode="HTML")
        return

    card = await add_credit_topup(message.from_user.id, card_id, amount, message.text or "")
    await state.clear()
    if not card:
        await message.answer("Карта не найдена.")
        return
    await message.answer(
        "Пополнение записано: <b>" + rub(amount) + "</b>\n\n"
        + await _card_text(message.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card_id),
    )


@router.callback_query(F.data.startswith("credit_balance:"))
async def cb_credit_balance(call: CallbackQuery, state: FSMContext):
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await state.set_state(CreditState.waiting_balance)
    await state.update_data(credit_card_id=card_id)
    await call.message.edit_text(
        "Какой сейчас остаток долга по <b>" + escape(_card_title(card)) + "</b>?\n\n"
        "Пиши свободно:\n"
        "<code>остаток долга 83500, лимит 150000</code>\n\n"
        "Если лимит поменялся — можешь написать его в этой же фразе.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card_id}")],
        ]),
    )
    await call.answer()


@router.message(CreditState.waiting_balance)
async def msg_credit_balance(message: Message, state: FSMContext):
    if _cancel_text(message.text):
        data = await state.get_data()
        await state.clear()
        card = await get_credit_card(message.from_user.id, data.get("credit_card_id"))
        if card:
            await message.answer(await _card_text(message.from_user.id, card), parse_mode="HTML", reply_markup=_card_kb(card[0]))
        else:
            await _show_credits(message, message.from_user.id)
        return

    data = await state.get_data()
    card_id = int(data.get("credit_card_id"))
    payload = parse_balance_payload(message.text or "")
    if payload.get("debt_amount") is None:
        await message.answer("Не увидел остаток долга. Например: <code>остаток долга 83500</code>", parse_mode="HTML")
        return

    card = await update_credit_balance(
        message.from_user.id,
        card_id,
        payload["debt_amount"],
        payload.get("credit_limit"),
        message.text or "",
    )
    await state.clear()
    if not card:
        await message.answer("Карта не найдена.")
        return
    await message.answer(
        "Остаток обновлён.\n\n" + await _card_text(message.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card_id),
    )


@router.callback_query(F.data.startswith("credit_limit:"))
async def cb_credit_limit(call: CallbackQuery, state: FSMContext):
    card_id = int(call.data.split(":")[1])
    card = await get_credit_card(call.from_user.id, card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return
    await state.set_state(CreditState.waiting_limit)
    await state.update_data(credit_card_id=card_id)
    await call.message.edit_text(
        "Если банк увеличил лимит по <b>" + escape(_card_title(card)) + "</b>, напиши как есть:\n\n"
        "<code>лимит увеличили до 200000</code>\n\n"
        "Важно: увеличение лимита не считается прогрессом. Это просто новые условия карты.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"credit_view:{card_id}")],
        ]),
    )
    await call.answer()


@router.message(CreditState.waiting_limit)
async def msg_credit_limit(message: Message, state: FSMContext):
    if _cancel_text(message.text):
        data = await state.get_data()
        await state.clear()
        card = await get_credit_card(message.from_user.id, data.get("credit_card_id"))
        if card:
            await message.answer(await _card_text(message.from_user.id, card), parse_mode="HTML", reply_markup=_card_kb(card[0]))
        else:
            await _show_credits(message, message.from_user.id)
        return

    data = await state.get_data()
    card_id = int(data.get("credit_card_id"))
    amount = parse_limit_amount(message.text or "")
    if not amount:
        await message.answer("Не увидел новый лимит. Например: <code>лимит до 200000</code>", parse_mode="HTML")
        return

    card = await update_credit_limit(message.from_user.id, card_id, amount, message.text or "")
    await state.clear()
    if not card:
        await message.answer("Карта не найдена.")
        return
    await message.answer(
        "Лимит обновлён. Это не прогресс погашения, а новые условия карты.\n\n"
        + await _card_text(message.from_user.id, card),
        parse_mode="HTML",
        reply_markup=_card_kb(card_id),
    )


@router.message(F.text.regexp(CREDIT_FREEFORM_RE), StateFilter(default_state))
async def msg_credit_freeform(message: Message, state: FSMContext):
    text = message.text or ""
    cards = await list_credit_cards(message.from_user.id)
    if not cards:
        await message.answer(
            "Вижу, это про кредитку. Сначала добавь её в разделе «Кредиты», а потом я смогу обновлять остатки прямо из чата.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Открыть кредиты", callback_data="credits_menu")],
            ]),
        )
        return

    card = _find_card_from_text(cards, text)
    if not card:
        await message.answer(
            "Понял, это про кредитку, но не понял какую. Открой раздел и выбери карту.",
            reply_markup=_choose_card_kb(cards, "credit_balance"),
        )
        return

    card_id = int(card[0])
    lower = text.lower()
    topup_like = any(word in lower for word in ("пополнил", "пополнение", "внес", "внёс", "закинул", "погасил"))
    balance_like = any(word in lower for word in ("остаток", "долг", "задолженность", "тело"))
    limit_like = "лимит" in lower

    if topup_like:
        amount = parse_topup_amount(text)
        if not amount:
            await message.answer("Не увидел сумму пополнения. Например: <code>внёс 10000 на карту</code>", parse_mode="HTML")
            return
        updated = await add_credit_topup(message.from_user.id, card_id, amount, text)
        await message.answer(
            "Пополнение кредитки записано: <b>" + rub(amount) + "</b>\n\n"
            + await _card_text(message.from_user.id, updated),
            parse_mode="HTML",
            reply_markup=_card_kb(card_id),
        )
        return

    if balance_like:
        payload = parse_balance_payload(text)
        if payload.get("debt_amount") is None:
            await message.answer("Не увидел остаток долга. Например: <code>остаток долга 83500</code>", parse_mode="HTML")
            return
        updated = await update_credit_balance(
            message.from_user.id,
            card_id,
            payload["debt_amount"],
            payload.get("credit_limit"),
            text,
        )
        await message.answer(
            "Остаток по кредитке обновлён.\n\n" + await _card_text(message.from_user.id, updated),
            parse_mode="HTML",
            reply_markup=_card_kb(card_id),
        )
        return

    if limit_like:
        amount = parse_limit_amount(text)
        if not amount:
            await message.answer("Не увидел новый лимит. Например: <code>лимит увеличили до 200000</code>", parse_mode="HTML")
            return
        updated = await update_credit_limit(message.from_user.id, card_id, amount, text)
        await message.answer(
            "Лимит обновлён. Это новые условия карты, не прогресс погашения.\n\n"
            + await _card_text(message.from_user.id, updated),
            parse_mode="HTML",
            reply_markup=_card_kb(card_id),
        )
