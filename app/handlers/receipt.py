from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import httpx
import base64
import os

router = Router()


async def scan_receipt_with_ai(image_bytes: bytes) -> str:
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    system_prompt = (
        "Найди в чеке строку ИТОГ или ИТОГО и верни ровно одну строку:\n"
        "TRANSACTION: -СУММА КАТЕГОРИЯ бн\n"
        "Где СУММА — число из строки ИТОГ/ИТОГО, КАТЕГОРИЯ — одно из: Еда/Продукты, Транспорт, Здоровье, Одежда, Развлечения, Прочие расходы.\n"
        "Если оплата наличными — пиши нал вместо бн.\n"
        "Отвечай ТОЛЬКО этой одной строкой. Ноль слов кроме неё.\n"
        "Пример: TRANSACTION: -1112.53 Еда/Продукты бн"
    )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 500,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64," + image_b64,
                                    "detail": "high"
                                }
                            },
                            {
                                "type": "text",
                                "text": "Распознай чек и верни транзакции в формате TRANSACTION:"
                            }
                        ]
                    }
                ],
            },
            timeout=30.0
        )
        data = response.json()

    return data["choices"][0]["message"]["content"]


@router.message(F.photo)
async def msg_photo_receipt(message: Message):
    from app.database import get_user_tier, get_categories, add_transaction
    from app.parser import parse_quick_input

    tier = await get_user_tier(message.from_user.id)
    if tier == 'free':
        await message.answer(
            "Сканирование чеков доступно с тарифа Старт.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            ])
        )
        return

    thinking = await message.answer("Читаю чек...")

    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_bytes = file_bytes.read()

        # Отправляем в GPT-4o Vision
        result = await scan_receipt_with_ai(image_bytes)

        await thinking.delete()

        # Парсим транзакции
        categories = await get_categories(message.from_user.id)
        added = []
        lines = result.strip().split("\n")

        import re as _re
        for line in lines:
            if "TRANSACTION:" in line:
                tx_str = line[line.find("TRANSACTION:")+len("TRANSACTION:"):].strip()
                try:
                    # Ищем число в строке
                    m = _re.search(r"[-]?(\d+(?:[.,]\d+)?)", tx_str)
                    if not m:
                        continue
                    amount = float(m.group(1).replace(",", "."))
                    if amount <= 0:
                        continue

                    # Определяем категорию по ключевым словам
                    tx_lower = tx_str.lower()
                    hint = ""
                    if any(w in tx_lower for w in ["еда", "продукт", "магнит", "пятёрочка", "перекрёсток", "лента", "ашан"]):
                        hint = "еда"
                    elif any(w in tx_lower for w in ["транспорт", "такси", "автобус", "метро"]):
                        hint = "транспорт"
                    elif any(w in tx_lower for w in ["здоровье", "аптека", "больница"]):
                        hint = "здоровье"
                    elif any(w in tx_lower for w in ["одежда"]):
                        hint = "одежда"

                    category_id = None
                    category_name = ''
                    # Ищем по hint
                    for cat in categories:
                        if hint and hint.lower() in cat['name'].lower() and cat.get('type') == 'expense':
                            category_id = cat['id']
                            category_name = cat['name']
                            break
                    # Фоллбек на Еда/Продукты
                    if not category_id:
                        for cat in categories:
                            if 'еда' in cat['name'].lower() and cat.get('type') == 'expense':
                                category_id = cat['id']
                                category_name = cat['name']
                                break
                    # Фоллбек на Прочие расходы
                    if not category_id:
                        for cat in categories:
                            if 'прочие' in cat['name'].lower() and cat.get('type') == 'expense':
                                category_id = cat['id']
                                category_name = cat['name']
                                break
                    if not category_id:
                        for cat in categories:
                            if cat.get('type') == 'expense':
                                category_id = cat['id']
                                category_name = cat['name']
                                break

                    if category_id:
                        await add_transaction(
                            message.from_user.id,
                            category_id=category_id,
                            amount=amount,
                            type_='expense',
                            kind='variable',
                            comment='Чек'
                        )
                        added.append("-" + str(int(amount)) + " руб. — " + category_name)
                except Exception:
                    pass

        if added:
            text = "Чек распознан! Внесено:\n" + "\n".join(added)
        else:
            text = "Не удалось распознать транзакции из чека.\nПопробуй сделать более чёткое фото."

        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )

    except Exception as e:
        await thinking.delete()
        await message.answer("Ошибка при распознавании: " + str(e))
