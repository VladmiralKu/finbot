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
            InlineKeyboardButton(text="🤖 ИИ-ассистент", callback_data="ai_assistant"),
            InlineKeyboardButton(text="📷 Фото чека",   callback_data="scan_receipt"),
        ],
        [
            InlineKeyboardButton(text="🗓 Календарь", callback_data="calendar"),
            InlineKeyboardButton(text="⭐ Тарифы",    callback_data="premium"),
        ],
        [
            InlineKeyboardButton(text="💼 Бизнес", callback_data="business_tools"),
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
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="main_menu")])
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
    if tier == 'business':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Business активен", callback_data="noop")],
            [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
        ])
    if tier == 'premium':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Premium активен", callback_data="noop")],
            [InlineKeyboardButton(text="Upgrade до Business — 300 Stars", callback_data="buy_stars_business")],
            [InlineKeyboardButton(text="Ввести промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
        ])
    if tier == 'start':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Старт активен", callback_data="noop")],
            [InlineKeyboardButton(text="Upgrade до Premium — 150 Stars", callback_data="buy_stars_premium")],
            [InlineKeyboardButton(text="Upgrade до Business — 300 Stars", callback_data="buy_stars_business")],
            [InlineKeyboardButton(text="Ввести промокод", callback_data="enter_promo")],
            [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
        ])
    # free
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Старт — 79 руб/мес", callback_data="buy_stars_start")],
        [InlineKeyboardButton(text="Premium — 150 Stars/мес", callback_data="buy_stars_premium")],
        [InlineKeyboardButton(text="Business — 300 Stars/мес", callback_data="buy_stars_business")],
        [InlineKeyboardButton(text="Ввести промокод", callback_data="enter_promo")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])
