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
        await message.answer("GPT: " + result[:300])

        # Парсим транзакции
        categories = await get_categories(message.from_user.id)
        added = []
        lines = result.strip().split("\n")

        import re as _re

        # Ищем итоговую сумму в ответе GPT
        amount_found = None
        # Сначала ищем БЕЗНАЛИЧНЫМИ — это всегда финальная сумма
        for pattern in [
            r"БЕЗНАЛИЧНЫМИ?[^\d]*(\d{3,}(?:[.,]\d{2})?)",
            r"безналичными?[^\d]*(\d{3,}(?:[.,]\d{2})?)",
        ]:
            m = _re.search(pattern, result, _re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1).replace(",", "."))
                    if val > 0:
                        amount_found = val
                        break
                except Exception:
                    continue

        # Если не нашли — берём последнее вхождение ИТОГ
        if not amount_found:
            matches = list(_re.finditer(r"ИТОГ[^\d]*(\d{3,}(?:[.,]\d{2})?)", result, _re.IGNORECASE))
            if matches:
                try:
                    amount_found = float(matches[-1].group(1).replace(",", "."))
                except Exception:
                    pass

        # Фоллбек — Total
        if not amount_found:
            m = _re.search(r"Total[^\d]*(\d{3,}(?:[.,]\d{2})?)", result, _re.IGNORECASE)
            if m:
                try:
                    amount_found = float(m.group(1).replace(",", "."))
                except Exception:
                    pass

        if amount_found:
            category_id = None
            category_name = ""
            for cat in categories:
                if "еда" in cat["name"].lower() and cat.get("type") == "expense":
                    category_id = cat["id"]
                    category_name = cat["name"]
                    break
            if not category_id:
                for cat in categories:
                    if "прочие" in cat["name"].lower() and cat.get("type") == "expense":
                        category_id = cat["id"]
                        category_name = cat["name"]
                        break
            if not category_id:
                for cat in categories:
                    if cat.get("type") == "expense":
                        category_id = cat["id"]
                        category_name = cat["name"]
                        break
            if category_id:
                await add_transaction(
                    message.from_user.id,
                    category_id=category_id,
                    amount=amount_found,
                    type_="expense",
                    kind="variable",
                    comment="Чек"
                )
                added.append("-" + str(int(amount_found)) + " руб. — " + category_name)

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
