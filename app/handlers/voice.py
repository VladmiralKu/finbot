from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import httpx
import os

router = Router()


async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    import io
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
            },
            files={
                "file": (filename, audio_bytes, "audio/ogg"),
            },
            data={
                "model": "whisper-1",
                "language": "ru",
            },
            timeout=30.0
        )
        data = response.json()
    return data.get("text", "")


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
        # Скачиваем голосовое
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        audio_bytes = file_bytes.read()

        # Транскрибируем через Whisper
        text = await transcribe_voice(audio_bytes)

        if not text:
            await thinking.delete()
            await message.answer("Не удалось распознать голос. Попробуй ещё раз.")
            return

        await thinking.delete()
        await message.answer("Распознано: " + text)

        # Пробуем распарсить как транзакцию
        categories = await get_categories(message.from_user.id)
        parsed = parse_quick_input(text, categories)

        if parsed:
            tx_id = await add_transaction(message.from_user.id, **parsed)
            sign = "-" if parsed.get('type') == 'expense' else "+"
            amount = parsed.get('amount', '')
            cat = parsed.get('category_name', '')
            await message.answer(
                "Записано: " + sign + str(amount) + " руб. — " + str(cat),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
        else:
            # Если не транзакция — отправляем в ИИ-ассистент
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
