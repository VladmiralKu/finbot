from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

router = Router()

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


class NoteState(StatesGroup):
    waiting_text = State()


class NoteDeleteState(StatesGroup):
    waiting_id = State()


async def _save_note(user_id: int, text: str):
    from app.database import execute
    await execute(
        "INSERT INTO notes (user_id, text) VALUES (%s, %s)",
        (user_id, text),
    )


def business_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Табло управленца", callback_data="dash_menu")],
        [InlineKeyboardButton(text="ПнЛ отчёт", callback_data="pnl_menu")],
        [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])


@router.callback_query(F.data == "business_tools")
async def cb_business_tools(call: CallbackQuery):
    await call.message.edit_text(
        "Бизнес инструменты:",
        parse_mode=None,
        reply_markup=business_menu_kb()
    )


# --- Dashboard menu ---

@router.callback_query(F.data == "dash_menu")
async def cb_dash_menu(call: CallbackQuery):
    now = datetime.now()
    months = []
    for i in range(3):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(
            text=MONTHS_RU[m] + " " + str(y),
            callback_data="dash:" + str(y) + ":" + str(m)
        )] for y, m in months],
        [InlineKeyboardButton(text="Выгрузить за год в Excel", callback_data="dashboard_export_year")],
        [InlineKeyboardButton(text="Назад", callback_data="business_tools")],
    ])
    await call.message.edit_text("Табло управленца - выбери месяц:", parse_mode=None, reply_markup=kb)


# --- PnL menu ---

@router.callback_query(F.data == "pnl_menu")
async def cb_pnl_menu(call: CallbackQuery):
    now = datetime.now()
    pm = now.month - 1
    py = now.year
    if pm <= 0:
        pm += 12
        py -= 1
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Текущий - " + MONTHS_RU[now.month] + " " + str(now.year),
            callback_data="pnl:" + str(now.year) + ":" + str(now.month)
        )],
        [InlineKeyboardButton(
            text="Прошлый - " + MONTHS_RU[pm] + " " + str(py),
            callback_data="pnl:" + str(py) + ":" + str(pm)
        )],
        [InlineKeyboardButton(text="Выгрузить за год в Excel", callback_data="pnl_export_year")],
        [InlineKeyboardButton(text="Назад", callback_data="business_tools")],
    ])
    await call.message.edit_text("ПнЛ отчёт - выбери период:", parse_mode=None, reply_markup=kb)


# --- Notes ---

@router.callback_query(F.data == "notes_menu")
async def cb_notes_menu(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Записать мысль", callback_data="note_add")],
        [InlineKeyboardButton(text="Старые заметки", callback_data="notes_list:0")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text(
        "📝 <b>Заметки</b>\n\n"
        "Это место для важной информации, которая не является транзакцией: договорённости, планы, долги, идеи, цели, детали бизнеса.\n\n"
        "Важно: <b>ИИ-помощник видит заметки</b> и может использовать их как базу данных для отчётов, выводов, планов и финансового анализа.\n\n"
        "Можно написать текстом или надиктовать голосом.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "note_add")
async def cb_note_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(NoteState.waiting_text)
    await call.message.edit_text(
        "Напиши или надиктуй свою мысль:",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="notes_menu")]
        ])
    )


@router.message(NoteState.waiting_text, F.voice)
async def msg_note_voice(message: Message, state: FSMContext):
    from app.handlers.voice import transcribe_voice

    thinking = await message.answer("Распознаю заметку...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        text = await transcribe_voice(file_bytes.read())
        await thinking.delete()
    except Exception as e:
        await thinking.delete()
        await message.answer("Не удалось распознать голос: " + str(e))
        return

    await state.clear()
    if not text:
        await message.answer("Текст не получен, попробуй ещё раз.")
        return
    await _save_note(message.from_user.id, text)
    await message.answer(
        "Заметка сохранена!\n\n" + text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(NoteState.waiting_text, F.text)
async def msg_note_text(message: Message, state: FSMContext):
    await state.clear()
    text = message.text or ""
    if not text:
        await message.answer("Текст не получен, попробуй ещё раз.")
        return
    await _save_note(message.from_user.id, text)
    await message.answer(
        "Заметка сохранена!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data.startswith("notes_list:"))
async def cb_notes_list(call: CallbackQuery):
    from app.database import fetchall
    offset = int(call.data.split(":")[1])
    limit = 5

    rows = await fetchall(
        "SELECT id, created_at, text FROM notes WHERE user_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (call.from_user.id, limit + 1, offset)
    )

    if not rows:
        await call.message.edit_text(
            "Zametok poka net.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
            ])
        )
        return

    has_more = len(rows) > limit
    rows = rows[:limit]

    text = "Заметки:\n\n"
    for row in rows:
        note_id, created_at, note_text = row
        date_str = created_at.strftime("%d.%m.%Y %H:%M")
        preview = note_text[:80] + "..." if len(note_text) > 80 else note_text
        text += "#" + str(note_id) + " [" + date_str + "]\n" + preview + "\n\n"

    buttons = []
    if offset > 0:
        buttons.append(InlineKeyboardButton(text="< Назад", callback_data="notes_list:" + str(offset - limit)))
    if has_more:
        buttons.append(InlineKeyboardButton(text="Ещё >", callback_data="notes_list:" + str(offset + limit)))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        buttons if buttons else [],
        [InlineKeyboardButton(text="Найти по номеру", callback_data="note_search")],
        [InlineKeyboardButton(text="Удалить по номеру", callback_data="note_delete")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, parse_mode=None, reply_markup=kb)


class NoteSearchState(StatesGroup):
    waiting_id = State()


@router.callback_query(F.data == "note_search")
async def cb_note_search(call: CallbackQuery, state: FSMContext):
    await state.set_state(NoteSearchState.waiting_id)
    await call.message.edit_text(
        "Введи номер заметки (например: 42):",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="notes_menu")]
        ])
    )


@router.message(NoteSearchState.waiting_id)
async def msg_note_search(message: Message, state: FSMContext):
    from app.database import fetchone
    await state.clear()
    try:
        note_id = int(message.text.strip().replace("#", ""))
    except ValueError:
        await message.answer("Введи числовой номер заметки.")
        return

    row = await fetchone(
        "SELECT id, created_at, text FROM notes WHERE id=%s AND user_id=%s",
        (note_id, message.from_user.id)
    )

    if not row:
        await message.answer(
            "Zametka #" + str(note_id) + " ne naydena.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")]
            ])
        )
        return

    note_id, created_at, text = row
    date_str = created_at.strftime("%d.%m.%Y %H:%M")
    await message.answer(
        "#" + str(note_id) + " [" + date_str + "]\n\n" + text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить заметку", callback_data=f"note_delete_confirm:{note_id}")],
            [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data == "note_delete")
async def cb_note_delete(call: CallbackQuery, state: FSMContext):
    await state.set_state(NoteDeleteState.waiting_id)
    await call.message.answer(
        "Введи номер заметки для удаления, например: 42.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="notes_menu")]
        ])
    )


@router.message(NoteDeleteState.waiting_id, F.text)
async def msg_note_delete(message: Message, state: FSMContext):
    await state.clear()
    try:
        note_id = int((message.text or "").strip().replace("#", ""))
    except ValueError:
        await message.answer("Введи числовой номер заметки.")
        return
    await _delete_note(message.from_user.id, note_id, message)


@router.message(NoteDeleteState.waiting_id, F.voice)
async def msg_note_delete_voice(message: Message, state: FSMContext):
    from app.handlers.voice import transcribe_voice
    import re

    thinking = await message.answer("Распознаю номер заметки...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        text = await transcribe_voice(file_bytes.read())
        await thinking.delete()
    except Exception as e:
        await thinking.delete()
        await message.answer("Не удалось распознать голос: " + str(e))
        return

    await state.clear()
    match = re.search(r"\d+", text or "")
    if not match:
        await message.answer("Не услышал номер заметки. Введи номер текстом.")
        return
    await _delete_note(message.from_user.id, int(match.group(0)), message)


@router.callback_query(F.data.startswith("note_delete_confirm:"))
async def cb_note_delete_confirm(call: CallbackQuery):
    note_id = int(call.data.split(":")[1])
    await _delete_note(call.from_user.id, note_id, call.message, edit=True)


async def _delete_note(user_id: int, note_id: int, target, edit: bool = False):
    from app.database import execute
    row_count = await execute(
        "DELETE FROM notes WHERE id=%s AND user_id=%s",
        (note_id, user_id),
    )
    text = f"Заметка #{note_id} удалена." if row_count else f"Заметка #{note_id} не найдена."
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
    if edit:
        await target.edit_text(text, parse_mode=None, reply_markup=reply_markup)
    else:
        await target.answer(text, parse_mode=None, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("dash:"))
async def cb_dash_by_month(call: CallbackQuery):
    from app.database import get_dashboard, can_use_feature
    if not await can_use_feature(call.from_user.id, 'business_tools'):
        await call.message.edit_text(
            "Табло управленца доступно на тарифе Business.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    d = await get_dashboard(call.from_user.id, year, month)

    if d['dynamics'] is not None:
        dyn_icon = "+" if d['dynamics'] >= 0 else ""
        dyn_str = dyn_icon + str(round(d['dynamics'], 1)) + "% vs прошлый месяц"
    else:
        dyn_str = "Нет данных за прошлый месяц"

    top_cats = sorted([c for c in d['categories'] if c[1] in ('fixed','variable')],
                      key=lambda x: x[2], reverse=True)[:3]
    top_str = ""
    if top_cats:
        top_str = "\nТоп расходов:\n"
        for name, kind, total in top_cats:
            pct = total / d['income'] * 100 if d['income'] > 0 else 0
            top_str += "  " + name + ": " + "{:,.0f}".format(total) + " (" + str(round(pct,1)) + "%)\n"

    upcoming_str = ""
    if d['upcoming']:
        upcoming_str = "\nБлижайшие платежи (7 дней):\n"
        for p in d['upcoming']:
            upcoming_str += "  " + p[2].strftime('%d.%m') + " " + p[0] + " — " + "{:,.0f}".format(float(p[1])) + " руб.\n"

    net_icon = "✅" if d['net_profit'] >= 0 else "🔴"
    text = (
        "Табло управленца — " + MONTHS_RU[month] + " " + str(year) + "\n\n"
        + "Выручка: " + "{:,.0f}".format(d['income']) + " руб.\n"
        + "Переменные расходы: -" + "{:,.0f}".format(d['variable_expense']) + " руб.\n"
        + "Постоянные расходы: -" + "{:,.0f}".format(d['fixed_expense']) + " руб.\n\n"
        + "EBITDA: " + "{:,.0f}".format(d['ebitda']) + " руб.\n"
    )
    if d['depreciation'] > 0:
        text += "  Амортизация: -" + "{:,.0f}".format(d['depreciation']) + "\n"
    if d['tax'] > 0:
        text += "  Налоги: -" + "{:,.0f}".format(d['tax']) + "\n"
    if d['loan_body'] > 0 or d['loan_pct'] > 0:
        text += "  Кредиты: -" + "{:,.0f}".format(d['loan_body']+d['loan_pct']) + "\n"

    text += (
        "\n" + net_icon + " Чистая прибыль: " + "{:,.0f}".format(d['net_profit'])
        + " руб. (" + str(round(d['net_profit_pct'], 1)) + "%)\n"
        + dyn_str + "\n"
        + "\nТранзакций: " + str(d['tx_count'])
        + top_str + upcoming_str
    )

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="dash_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.callback_query(F.data.startswith("pnl:"))
async def cb_pnl_by_month(call: CallbackQuery):
    from app.database import get_pnl_report, can_use_feature
    if not await can_use_feature(call.from_user.id, 'pnl_table'):
        await call.message.edit_text(
            "ПнЛ отчёт доступен на тарифе Premium и выше.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    parts = call.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    d = await get_pnl_report(call.from_user.id, year, month)

    def pct(val):
        if d['income'] > 0:
            return str(round(val / d['income'] * 100, 1)) + "%"
        return "—"

    text = "ПнЛ — " + MONTHS_RU[month] + " " + str(year) + "\n\n"
    text += "ВЫРУЧКА: " + "{:,.0f}".format(d['income']) + " руб.\n"
    for name, total in d['income_cats']:
        text += "  " + name + ": " + "{:,.0f}".format(total) + " (" + pct(total) + ")\n"

    text += "\nПЕРЕМЕННЫЕ РАСХОДЫ: -" + "{:,.0f}".format(d['variable']) + " (" + pct(d['variable']) + ")\n"
    for name, total in d['variable_cats']:
        text += "  " + name + ": -" + "{:,.0f}".format(total) + " (" + pct(total) + ")\n"

    gp_sign = "+" if d['gross_profit'] >= 0 else ""
    text += "\nМаржинальная прибыль: " + gp_sign + "{:,.0f}".format(d['gross_profit']) + " (" + pct(d['gross_profit']) + ")\n"

    text += "\nПОСТОЯННЫЕ РАСХОДЫ: -" + "{:,.0f}".format(d['fixed']) + " (" + pct(d['fixed']) + ")\n"
    for name, total in d['fixed_cats']:
        text += "  " + name + ": -" + "{:,.0f}".format(total) + " (" + pct(total) + ")\n"

    eb_sign = "+" if d['ebitda'] >= 0 else ""
    text += "\nEBITDA: " + eb_sign + "{:,.0f}".format(d['ebitda']) + " (" + pct(d['ebitda']) + ")\n"

    if d['depreciation'] > 0:
        text += "  Амортизация: -" + "{:,.0f}".format(d['depreciation']) + "\n"
    if d['tax'] > 0:
        text += "  Налоги: -" + "{:,.0f}".format(d['tax']) + "\n"
    if d['loan_body'] > 0:
        text += "  Кредит (тело): -" + "{:,.0f}".format(d['loan_body']) + "\n"
    if d['loan_pct'] > 0:
        text += "  Кредит (проценты): -" + "{:,.0f}".format(d['loan_pct']) + "\n"

    np_sign = "+" if d['net_profit'] >= 0 else ""
    text += "\nЧИСТАЯ ПРИБЫЛЬ: " + np_sign + "{:,.0f}".format(d['net_profit']) + " (" + pct(d['net_profit']) + ")\n"

    await call.message.edit_text(
        text,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="pnl_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )
