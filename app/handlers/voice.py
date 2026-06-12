from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state
import httpx
import os

router = Router()


async def transcribe_voice(audio_bytes: bytes) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")},
            files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
            data={"model": "whisper-1", "language": "ru"},
            timeout=30.0
        )
        return response.json().get("text", "")


async def get_usd_rate() -> float:
    """Получаем курс доллара к рублю."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.exchangerate-api.com/v4/latest/USD",
                timeout=5.0
            )
            data = response.json()
            return data["rates"]["RUB"]
    except Exception:
        return 90.0  # фоллбек если API недоступен


async def parse_voice_to_transaction(text: str, categories: list = None) -> list:
    """Просим GPT извлечь все транзакции из текста."""
    cat_list = ""
    if categories:
        cat_list = "\nДоступные категории: " + ", ".join([c["name"] for c in categories])

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Извлеки ВСЕ транзакции из текста. Каждая на новой строке в формате: [+-]сумма категория\n"
                            "ВАЖНО: Расход = минус (-), Доход = плюс (+).\n"
                            "Слова-признаки ДОХОДА: заработал, получил, пришло, доход, зарплата, продал, выручка, перевели, начислили.\n"
                            "Слова-признаки РАСХОДА: потратил, купил, заплатил, оплатил, расход, списали, вышло, стоит, стоило.\n"
                            "Категорию выбирай ТОЧНО из списка доступных категорий (если есть).\n"
                            + cat_list + "\n"
                            "Примеры:\n"
                            "- '500 на продукты и 300 на кофе' →\n-500 Еда / Продукты\n-300 Еда / Продукты\n"
                            "- 'заработал 50000 на темке' →\n+50000 Фриланс\n"
                            "- 'получил зарплату 80000 и потратил 1000 на такси' →\n+80000 Зарплата\n-1000 Транспорт\n"
                            "Верни ТОЛЬКО строки транзакций, без пояснений."
                        )
                    },
                    {"role": "user", "content": text}
                ],
            },
            timeout=15.0
        )
        data = response.json()
        result = data["choices"][0]["message"]["content"].strip()
        return [line.strip() for line in result.split("\n") if line.strip()]


@router.message(F.voice, StateFilter(default_state))
async def msg_voice(message: Message):
    from app.database import get_user_tier, get_categories, add_transaction
    from app.parser import parse_quick_input

    tier = await get_user_tier(message.from_user.id)
    if tier == 'free':
        await message.answer(
            "Голосовой ввод доступен с тарифа Старт.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            ])
        )
        return

    thinking = await message.answer("Распознаю голос...")

    try:
        file = await message.bot.get_file(message.voice.file_id)
        await thinking.edit_text("Скачиваю аудио...")
        file_bytes = await message.bot.download_file(file.file_path)
        audio_bytes = file_bytes.read()
        await thinking.edit_text("Отправляю в Whisper...")
        text = await transcribe_voice(audio_bytes)
        if not text:
            await thinking.delete()
            await message.answer("Не удалось распознать голос. Попробуй ещё раз.")
            return

        await thinking.delete()
        await message.answer("Распознано: " + text)

        # Преобразуем в формат транзакций через GPT
        categories = await get_categories(message.from_user.id)
        tx_lines = await parse_voice_to_transaction(text, categories)
        added = []

        # Проверяем нужна ли конвертация валюты
        usd_rate = None
        if any(w in text.lower() for w in ['долларов', 'доллар', 'доллара', '$', 'usd']):
            usd_rate = await get_usd_rate()

        for tx_str in tx_lines:
            parsed = parse_quick_input(tx_str)
            if not parsed or not parsed.get('amount'):
                continue

            amount = parsed.get('amount')
            type_ = parsed.get('type', 'expense')
            hint = parsed.get('category_hint', '')

            # Конвертируем если нужно
            if usd_rate and amount:
                original = amount
                amount = round(amount * usd_rate)
                hint_currency = " (≈$" + str(int(original)) + ")"
            else:
                hint_currency = ""

            category_id = None
            category_name = ''
            for cat in categories:
                if hint and hint.lower() in cat['name'].lower():
                    category_id = cat['id']
                    category_name = cat['name']
                    break

            if not category_id:
                # Сначала ищем "Прочие расходы"
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
                    kind=parsed.get('kind', 'variable'),
                    comment=parsed.get('comment', '')
                )
                sign = "-" if type_ == 'expense' else "+"
                added.append(sign + str(int(amount)) + " руб. — " + category_name + hint_currency)

        if added:
            await message.answer(
                "Записано " + str(len(added)) + " транзакций:\n" + "\n".join(added),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
        else:
            # Не транзакция — отправляем в ИИ-ассистент
            from app.handlers.ai_assistant import get_ai_response, log_ai_usage
            thinking2 = await message.answer("Отправляю в ИИ-ассистент...")
            try:
                ai_text, _ = await get_ai_response(message.from_user.id, text, [])
                await log_ai_usage(message.from_user.id)
                await thinking2.delete()
                await message.answer(
                    ai_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                    ])
                )
            except Exception as e:
                await thinking2.delete()
                await message.answer("Ошибка ИИ: " + str(e))

    except Exception as e:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer("Ошибка: " + str(e))
