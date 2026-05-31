import re
from datetime import date, timedelta
from typing import Optional


MONTHS_RU = {
    'январь': 1, 'января': 1, 'jan': 1,
    'февраль': 2, 'февраля': 2, 'feb': 2,
    'март': 3, 'марта': 3, 'mar': 3,
    'апрель': 4, 'апреля': 4, 'apr': 4,
    'май': 5, 'мая': 5, 'may': 5,
    'июнь': 6, 'июня': 6, 'jun': 6,
    'июль': 7, 'июля': 7, 'jul': 7,
    'август': 8, 'августа': 8, 'aug': 8,
    'сентябрь': 9, 'сентября': 9, 'sep': 9,
    'октябрь': 10, 'октября': 10, 'oct': 10,
    'ноябрь': 11, 'ноября': 11, 'nov': 11,
    'декабрь': 12, 'декабря': 12, 'dec': 12,
}

WALLETS = {
    'нал': 'cash', 'наличные': 'cash', 'наличка': 'cash', 'cash': 'cash',
    'бн': 'card', 'безнал': 'card', 'карта': 'card', 'card': 'card',
    'другое': 'other', 'др': 'other', 'other': 'other',
}

CATEGORY_HINTS = {
    'продукт': 'Еда / Продукты', 'еда': 'Еда / Продукты', 'магазин': 'Еда / Продукты',
    'такси': 'Транспорт', 'метро': 'Транспорт', 'бензин': 'Транспорт', 'транспорт': 'Транспорт',
    'аренда': 'Аренда / Ипотека', 'ипотека': 'Аренда / Ипотека', 'квартира': 'Аренда / Ипотека',
    'коммунал': 'Коммуналка', 'жкх': 'Коммуналка', 'свет': 'Коммуналка', 'вода': 'Коммуналка',
    'кредит': 'Кредиты', 'займ': 'Кредиты',
    'подписк': 'Подписки', 'netflix': 'Подписки', 'spotify': 'Подписки',
    'одежд': 'Одежда', 'обувь': 'Одежда',
    'кино': 'Развлечения', 'ресторан': 'Развлечения', 'кафе': 'Развлечения', 'бар': 'Развлечения',
    'аптек': 'Здоровье', 'врач': 'Здоровье', 'лекарств': 'Здоровье',
    'зарплат': 'Зарплата', 'аванс': 'Зарплата',
    'фриланс': 'Фриланс', 'проект': 'Фриланс',
}


def parse_quick_input(text: str) -> dict:
    """
    Парсит строку быстрого ввода.
    Примеры:
    -1500 вчера за продукты купил маме бн на июнь
    +50000 зарплата нал
    -300 сегодня кофе
    """
    result = {
        'amount': None,
        'type': None,
        'transaction_date': date.today(),
        'category_hint': None,
        'comment': '',
        'wallet': 'card',
        'pnl_period': None,
    }

    text = text.strip()

    # Сумма (обязательно)
    amount_match = re.match(r'^([+-]?\d+(?:[.,]\d+)?)', text)
    if not amount_match:
        return None

    amount_str = amount_match.group(1).replace(',', '.')
    amount = float(amount_str.lstrip('+-'))
    result['amount'] = amount

    if text.startswith('-'):
        result['type'] = 'expense'
    elif text.startswith('+'):
        result['type'] = 'income'
    else:
        result['type'] = 'expense'  # по умолчанию расход

    # Убираем сумму из текста
    remaining = text[amount_match.end():].strip()
    # Убираем слова-связки
    remaining = re.sub(r'\b(за|на|купил|купила|потратил|заплатил|получил)\b', ' ', remaining, flags=re.IGNORECASE)

    words = remaining.lower().split()
    used_words = set()

    # Дата
    for i, word in enumerate(words):
        if word == 'сегодня':
            result['transaction_date'] = date.today()
            used_words.add(i)
        elif word == 'вчера':
            result['transaction_date'] = date.today() - timedelta(days=1)
            used_words.add(i)
        elif word == 'позавчера':
            result['transaction_date'] = date.today() - timedelta(days=2)
            used_words.add(i)
        elif re.match(r'^\d{1,2}\.\d{1,2}$', word):
            try:
                parts = word.split('.')
                result['transaction_date'] = date(date.today().year, int(parts[1]), int(parts[0]))
                used_words.add(i)
            except:
                pass

    # Кошелёк
    for i, word in enumerate(words):
        if word in WALLETS:
            result['wallet'] = WALLETS[word]
            used_words.add(i)

    # ПнЛ период (на + месяц)
    for i, word in enumerate(words):
        if word in MONTHS_RU:
            month_num = MONTHS_RU[word]
            year = date.today().year
            result['pnl_period'] = f"{year}-{month_num:02d}"
            used_words.add(i)
            if i > 0 and words[i-1] == 'на':
                used_words.add(i-1)

    # Категория
    for i, word in enumerate(words):
        if i in used_words:
            continue
        for hint, category in CATEGORY_HINTS.items():
            if hint in word:
                result['category_hint'] = category
                used_words.add(i)
                break

    # Комментарий — всё остальное
    comment_words = [words[i] for i in range(len(words)) if i not in used_words]
    result['comment'] = ' '.join(comment_words).strip()

    # Если категория не найдена явно — оставляем None, БД подберёт первую
    return result
