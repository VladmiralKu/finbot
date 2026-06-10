from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
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


async def parse_voice_to_transaction(text: str) -> str:
    """Просим GPT преобразовать текст в формат транзакции."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 50,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Преобразуй текст в формат транзакции: [+-]сумма категория\n"
                            "Расход = минус, доход = плюс.\n"
                            "Примеры:\n"
                            "- '500 рублей на продукты' → '-500 продукты'\n"
                            "- 'заработал 50000' → '+50000 доходы'\n"
                            "- 'потратил 300 на кофе' → '-300 кофе'\n"
                            "Верни ТОЛЬКО строку транзакции, без пояснений."
                        )
                    },
                    {"role": "user", "content": text}
                ],
            },
            timeout=15.0
        )
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


@router.message(F.voice)
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

        # Преобразуем в формат транзакции через GPT
        tx_str = await parse_voice_to_transaction(text)
        categories = await get_categories(message.from_user.id)
        parsed = parse_quick_input(tx_str)

        if parsed and parsed.get('amount'):
            tx_id = await add_transaction(message.from_user.id, **parsed)
            sign = "-" if parsed.get('type') == 'expense' else "+"
            amount = parsed.get('amount', '')
            cat = parsed.get('category_name', '') or parsed.get('category_hint', '') or ''
            await message.answer(
                "Записано: " + sign + str(int(amount)) + " руб. — " + str(cat),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
        else:
            await message.answer(
                "Не похоже на транзакцию. Отправить в ИИ-ассистент?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Да, отправить ИИ", callback_data="ai_assistant")],
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )

    except Exception as e:
        await thinking.delete()
        await message.answer("Ошибка: " + str(e))
