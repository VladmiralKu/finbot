from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


MAIN_REPLY_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🏠 Меню")]],
    resize_keyboard=True,
    is_persistent=True,
)


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✍️ Ручной ввод", callback_data="manual_input"),
        ],
        [
            InlineKeyboardButton(text="📋 Последние", callback_data="recent"),
            InlineKeyboardButton(text="⭐ Тарифы",    callback_data="premium"),
        ],
        [
            InlineKeyboardButton(text="🤖 ИИ-помощник", callback_data="ai_assistant"),
            InlineKeyboardButton(text="📊 Отчёты",        callback_data="reports_menu"),
        ],
        [
            InlineKeyboardButton(text="📂 Категории", callback_data="categories_list"),
        ],
        [
            InlineKeyboardButton(text="📝 Заметки", callback_data="notes_menu"),
        ],
    ])


def manual_input_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Расход", callback_data="add_expense"),
            InlineKeyboardButton(text="💰 Доход", callback_data="add_income"),
        ],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])


def reports_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Календарь", callback_data="calendar")],
        [InlineKeyboardButton(text="📊 Отчёт ДДС", callback_data="report_month")],
        [InlineKeyboardButton(text="📥 Выгрузить всю базу", callback_data="export_all")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])


def categories_keyboard(categories, prefix, back_callback="main_menu"):
    buttons = []
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(
            text=cat["name"],
            callback_data=f"{prefix}:{cat['id']}:{cat['kind']}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_keyboard(tx_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Верно", callback_data=f"confirm:{tx_id}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delete_tx:{tx_id}"),
        ],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])


def premium_keyboard(tier: str = 'free'):
    from app.handlers.premium import premium_keyboard as _pk
    return _pk(tier)
