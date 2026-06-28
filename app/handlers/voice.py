from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
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


@router.message(F.voice)
async def msg_voice(message: Message, state: FSMContext):
    from app.database import get_user_tier
    from app.handlers.ai_assistant import AIState
    from app.handlers.business import NoteSearchState, NoteState
    from app.services.insights import build_first_transaction_insight
    from app.services.transaction_ai import extract_transactions_from_text
    from app.services.transaction_service import create_transaction

    current_state = await state.get_state()
    forbidden_states = {
        AIState.chatting.state,
        NoteState.waiting_text.state,
        NoteSearchState.waiting_id.state,
    }
    if current_state in forbidden_states:
        return

    tier = await get_user_tier(message.from_user.id)
    if tier in ('free', 'scan_text'):
        await message.answer(
            "Голосовой ввод недоступен на тарифе Скан и текст. Доступен с тарифа База.",
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

        transactions = await extract_transactions_from_text(message.from_user.id, text, source="voice")
        added = []
        saved_ids = []

        # Проверяем нужна ли конвертация валюты
        usd_rate = None
        if any(w in text.lower() for w in ['долларов', 'доллар', 'доллара', '$', 'usd']):
            usd_rate = await get_usd_rate()

        for tx in transactions:
            amount = tx.get("amount")
            type_ = tx.get("type", "expense")

            # Конвертируем если нужно
            if usd_rate and amount:
                original = amount
                amount = round(amount * usd_rate)
                hint_currency = " (≈$" + str(int(original)) + ")"
            else:
                hint_currency = ""

            saved = await create_transaction(
                user_id=message.from_user.id,
                category_id=tx["category_id"],
                amount=amount,
                type_=type_,
                kind=tx.get("kind"),
                comment=tx.get("comment") or text,
                transaction_date=tx.get("transaction_date"),
                pnl_period=tx.get("pnl_period"),
            )
            saved_ids.append(saved["id"])
            sign = "-" if type_ == 'expense' else "+"
            added.append(sign + str(int(amount)) + " руб. — " + tx["category_name"] + hint_currency + f" #{saved['id']}")

        if added:
            if current_state:
                await state.clear()
            response_text = "Записано " + str(len(added)) + " транзакций:\n" + "\n".join(added)
            insight = await build_first_transaction_insight(message.from_user.id, saved_ids)
            if insight:
                response_text += "\n\n" + insight
            await message.answer(
                response_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                ])
            )
        else:
            from app.handlers.main import (
                HELP_TRIGGERS,
                answer_help_question,
                handle_intent_message,
                send_text_to_ai_assistant,
            )
            text_lower = text.lower().strip()
            if any(trigger in text_lower for trigger in HELP_TRIGGERS):
                thinking2 = await message.answer("Сейчас расскажу...")
                try:
                    answer = await answer_help_question(text)
                    await thinking2.delete()
                    await message.answer(
                        "❓ " + answer,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
                        ])
                    )
                except Exception as e:
                    await thinking2.delete()
                    await message.answer("Ошибка: " + str(e))
                return

            handled = await handle_intent_message(message, state, text, source="voice")
            if not handled:
                await send_text_to_ai_assistant(message, state, message.from_user.id, text)

    except Exception as e:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer("Ошибка: " + str(e))
