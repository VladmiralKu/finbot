from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Расход", callback_data="add_expense"),
            InlineKeyboardButton(text="💰 Доход",  callback_data="add_income"),
        ],
        [
            InlineKeyboardButton(text="📊 Отчёт за месяц", callback_data="report_month"),
            InlineKeyboardButton(text="📋 Последние",       callback_data="recent"),
        ],
        [
            InlineKeyboardButton(text="🤖 ИИ-анализ",   callback_data="ai_analyze"),
            InlineKeyboardButton(text="📷 Фото чека",   callback_data="scan_receipt"),
        ],
        [
            InlineKeyboardButton(text="🗓 Календарь", callback_data="calendar"),
            InlineKeyboardButton(text="⭐ Premium",    callback_data="premium"),
        ],
    ])


def categories_keyboard(categories, prefix):
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
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_keyboard(tx_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Верно", callback_data=f"confirm:{tx_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tx:{tx_id}"),
        ],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
    ])


def premium_keyboard(is_prem):
    if is_prem:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ У вас Premium активен", callback_data="noop")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оплатить $3/мес", callback_data="pay_premium")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])
