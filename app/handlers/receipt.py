from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import httpx
import base64
import os

router = Router()


async def scan_receipt_with_ai(image_bytes: bytes) -> str:
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    system_prompt = (
        "Ты распознаёшь чеки. Найди ИТОГОВУЮ сумму покупки (строка ИТОГ, ИТОГО, ИТОГ К ОПЛАТЕ, БЕЗНАЛИЧНЫМИ или НАЛИЧНЫМИ — это последняя сумма в чеке).\n"
        "Верни ТОЛЬКО одну строку формата:\n"
        "TOTAL: СУММА\n"
        "Где СУММА — итоговое число БЕЗ разделителей тысяч (никаких запятых и пробелов между цифрами), "
        "с точкой как разделителем копеек. Никакого другого текста. Только TOTAL: и число.\n"
        "Пример правильного ответа: TOTAL: 7608.00\n"
        "Пример НЕПРАВИЛЬНОГО ответа: TOTAL: 7,608.00"
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
                                "text": "Найди итоговую сумму чека и верни в формате TOTAL: СУММА"
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

        # Парсим итоговую сумму из ответа GPT
        categories = await get_categories(message.from_user.id)
        added = []

        import re as _re

        amount_found = None
        m = _re.search(r"TOTAL:\s*([\d.,]+)", result, _re.IGNORECASE)
        if m:
            try:
                raw = m.group(1)
                # Убираем разделители тысяч (запятые), оставляем точку как десятичный разделитель
                raw = raw.replace(",", "")
                amount_found = float(raw)
            except Exception:
                pass

        if amount_found:
            category_id = None
            category_name = ""
            for cat in categories:
                if "еда" in cat["name"].lower() and cat["type"] == "expense":
                    category_id = cat["id"]
                    category_name = cat["name"]
                    break
            if not category_id:
                for cat in categories:
                    if "прочие" in cat["name"].lower() and cat["type"] == "expense":
                        category_id = cat["id"]
                        category_name = cat["name"]
                        break
            if not category_id:
                for cat in categories:
                    if cat["type"] == "expense":
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
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer("Ошибка при распознавании: " + str(e))
