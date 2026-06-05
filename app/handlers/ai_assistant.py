from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import httpx
import json

router = Router()


class AIState(StatesGroup):
    chatting = State()


async def get_ai_response(user_id: int, user_message: str, history: list) -> tuple[str, list]:
    from app.database import get_monthly_summary, get_categories, get_user_tier, fetchone
    import os

    now = datetime.now()
    summary = await get_monthly_summary(user_id, now.year, now.month)
    categories = await get_categories(user_id)

    cat_list = ", ".join([c["name"] for c in categories]) if categories else "нет категорий"

    system_prompt = (
        "Ты финансовый ИИ-ассистент в приложении Баланс. "
        "Помогаешь пользователю управлять личными финансами и финансами бизнеса. "
        "Отвечай кратко и по делу. Используй цифры из контекста.\n\n"
        "ТЕКУЩИЙ КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:\n"
        "Месяц: " + str(now.month) + "/" + str(now.year) + "\n"
        "Доходы за месяц: " + "{:,.0f}".format(summary['income']) + " руб.\n"
        "Расходы за месяц: " + "{:,.0f}".format(summary['total_expense']) + " руб.\n"
        "Баланс: " + "{:,.0f}".format(summary['balance']) + " руб.\n"
        "Категории: " + cat_list + "\n\n"
        "ВАЖНО: Если пользователь хочет внести транзакцию, выдели её в конце ответа в формате:\n"
        "TRANSACTION: -1500 продукты бн\n"
        "или\n"
        "TRANSACTION: +50000 зарплата бн\n"
        "Формат: знак+сумма категория кошелёк(бн/нал) [на месяц]\n"
        "Если транзакции нет - не пиши TRANSACTION."
    )

    messages = history[-10:] + [{"role": "user", "content": user_message}]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": system_prompt,
                "messages": messages,
            },
            timeout=30.0
        )
        data = response.json()

    ai_text = data["content"][0]["text"]
    new_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": ai_text},
    ]
    return ai_text, new_history


async def check_ai_limit(user_id: int) -> tuple[bool, int, int]:
    from app.database import fetchone, get_user_tier
    tier = await get_user_tier(user_id)
    if tier == 'free':
        return False, 0, 0

    limits = {'start': 50, 'premium': 200, 'business': 9999}
    limit = limits.get(tier, 0)

    now = datetime.now()
    row = await fetchone(
        """SELECT COUNT(*) FROM ai_usage
           WHERE user_id=%s
             AND EXTRACT(YEAR FROM used_at)=%s
             AND EXTRACT(MONTH FROM used_at)=%s""",
        (user_id, now.year, now.month)
    )
    used = int(row[0]) if row else 0
    return used < limit, used, limit


async def log_ai_usage(user_id: int):
    from app.database import execute
    await execute(
        "INSERT INTO ai_usage (user_id, used_at) VALUES (%s, NOW())",
        (user_id,)
    )


@router.callback_query(F.data == "ai_assistant")
async def cb_ai_assistant(call: CallbackQuery, state: FSMContext):
    from app.database import get_user_tier
    tier = await get_user_tier(call.from_user.id)
    if tier == 'free':
        await call.message.edit_text(
            "ИИ-ассистент доступен с тарифа Старт (99 руб/мес).",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    can, used, limit = await check_ai_limit(call.from_user.id)
    if not can:
        await call.message.edit_text(
            "Лимит ИИ-ассистента исчерпан на этот месяц.\nИспользовано: " + str(used) + "/" + str(limit),
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    await state.set_state(AIState.chatting)
    await state.update_data(history=[])

    limit_str = "безлимит" if limit >= 9999 else str(used) + "/" + str(limit)
    await call.message.edit_text(
        "ИИ-ассистент Баланс\n\n"
        "Могу помочь:\n"
        "- Внести транзакцию ('потратил 3500 на продукты')\n"
        "- Проанализировать расходы\n"
        "- Ответить на вопросы по финансам\n\n"
        "Сообщений использовано: " + limit_str + "\n\n"
        "Напиши что хочешь сделать:",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
        ])
    )


@router.callback_query(F.data == "ai_end")
async def cb_ai_end(call: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.keyboards import main_menu
    await call.message.edit_text(
        "Чат с ИИ-ассистентом завершён.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(AIState.chatting)
async def msg_ai_chat(message: Message, state: FSMContext):
    from app.parser import parse_transaction
    from app.database import get_categories, add_transaction

    can, used, limit = await check_ai_limit(message.from_user.id)
    if not can:
        await state.clear()
        await message.answer(
            "Лимит ИИ-ассистента исчерпан. Используй тариф выше для безлимитного доступа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    data = await state.get_data()
    history = data.get('history', [])

    await message.answer("Думаю...", parse_mode=None)

    try:
        ai_text, new_history = await get_ai_response(
            message.from_user.id, message.text or "", history
        )
        await log_ai_usage(message.from_user.id)
    except Exception as e:
        await message.answer("Ошибка ИИ: " + str(e))
        return

    await state.update_data(history=new_history[-20:])

    # Проверяем есть ли транзакция
    tx_added = ""
    if "TRANSACTION:" in ai_text:
        lines = ai_text.split("\n")
        clean_lines = []
        for line in lines:
            if line.startswith("TRANSACTION:"):
                tx_str = line.replace("TRANSACTION:", "").strip()
                try:
                    categories = await get_categories(message.from_user.id)
                    parsed = parse_transaction(tx_str, categories)
                    if parsed:
                        tx_id = await add_transaction(message.from_user.id, **parsed)
                        tx_added = "\n\nТранзакция внесена!"
                except Exception:
                    pass
            else:
                clean_lines.append(line)
        ai_text = "\n".join(clean_lines).strip()

    limit_str = "безлимит" if limit >= 9999 else str(used + 1) + "/" + str(limit)
    await message.answer(
        ai_text + tx_added + "\n\n[" + limit_str + "]",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
        ])
    )
