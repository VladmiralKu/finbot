from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import httpx
import base64
import os

router = Router()


async def scan_receipt_with_ai(image_bytes: bytes) -> str:
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    system_prompt = (
        "Ты помощник для распознавания чеков и накладных. "
        "Извлеки из изображения все позиции и суммы. "
        "Верни ТОЛЬКО список транзакций в формате:\n"
        "TRANSACTION: -[сумма] [категория] [нал/бн]\n\n"
        "Правила:\n"
        "- Если это чек из магазина — одна транзакция с итоговой суммой\n"
        "- Категорию выбери из: Еда/Продукты, Транспорт, Здоровье, Одежда, Развлечения, Прочие расходы\n"
        "- Кошелёк: бн (безнал) или нал (наличные)\n"
        "- Если не можешь определить — используй 'Прочие расходы' и 'бн'\n"
        "- Никаких пояснений, только строки TRANSACTION:\n\n"
        "Пример:\n"
        "TRANSACTION: -1547 Еда/Продукты бн"
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

        for line in lines:
            if line.startswith("TRANSACTION:"):
                tx_str = line.replace("TRANSACTION:", "").strip()
                try:
                    parsed = parse_quick_input(tx_str)
                    if parsed and parsed.get('amount'):
                        amount = parsed['amount']
                        type_ = parsed.get('type', 'expense')
                        hint = parsed.get('category_hint', '')
                        category_id = None
                        category_name = ''
                        for cat in categories:
                            if hint and hint.lower() in cat['name'].lower():
                                category_id = cat['id']
                                category_name = cat['name']
                                break
                        if not category_id:
                            for cat in categories:
                                if 'прочие' in cat['name'].lower() and cat.get('type') == type_:
                                    category_id = cat['id']
                                    category_name = cat['name']
                                    break
                        if not category_id:
                            for cat in categories:
                                if cat.get('type') == type_:
                                    category_id = cat['id']
                                    category_name = cat['name']
                                    break
                        if category_id:
                            await add_transaction(
                                message.from_user.id,
                                category_id=category_id,
                                amount=amount,
                                type_=type_,
                                kind='variable',
                                comment=parsed.get('comment', '')
                            )
                            sign = "-" if type_ == 'expense' else "+"
                            added.append(sign + str(int(amount)) + " руб. — " + category_name)
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
