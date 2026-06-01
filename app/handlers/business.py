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
        [InlineKeyboardButton(text="Назад", callback_data="business_tools")],
    ])
    await call.message.edit_text("Заметки:", parse_mode=None, reply_markup=kb)


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


@router.message(NoteState.waiting_text)
async def msg_note_text(message: Message, state: FSMContext):
    from app.database import execute
    await state.clear()
    text = message.text or ""
    if not text:
        await message.answer("Текст не получен, попробуй ещё раз.")
        return
    await execute(
        "INSERT INTO notes (user_id, text) VALUES (%s, %s)",
        (message.from_user.id, text)
    )
    await message.answer(
        "Заметка сохранена!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(NoteState.waiting_text, F.voice)
async def msg_note_voice(message: Message, state: FSMContext):
    from app.database import execute
    await state.clear()
    await execute(
        "INSERT INTO notes (user_id, text) VALUES (%s, %s)",
        (message.from_user.id, "[Golosovaya zametka - " + message.date.strftime("%d.%m.%Y %H:%M") + "]")
    )
    await message.answer(
        "Голосовая заметка сохранена! (расшифровка будет доступна в Premium)",
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
                [InlineKeyboardButton(text="Назад", callback_data="notes_menu")]
            ])
        )
        return

    has_more = len(rows) > limit
    rows = rows[:limit]

    text = "Zametki:\n\n"
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
        [InlineKeyboardButton(text="Назад", callback_data="notes_menu")],
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
            [InlineKeyboardButton(text="Заметки", callback_data="notes_menu")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )
