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


async def get_user_context(user_id: int) -> str:
    from app.database import get_monthly_summary, get_categories, fetchall, fetchone
    now = datetime.now()

    # Текущий месяц
    summary = await get_monthly_summary(user_id, now.year, now.month)
    categories = await get_categories(user_id)
    cat_list = ", ".join([c["name"] for c in categories]) if categories else "нет"

    # Прошлый месяц
    pm = now.month - 1
    py = now.year
    if pm <= 0:
        pm += 12
        py -= 1
    prev = await get_monthly_summary(user_id, py, pm)

    # Топ расходов
    top = await fetchall(
        """SELECT c.name, SUM(t.amount) as total
           FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s AND t.type = 'expense'
             AND EXTRACT(YEAR FROM t.transaction_date) = %s
             AND EXTRACT(MONTH FROM t.transaction_date) = %s
           GROUP BY c.name ORDER BY total DESC LIMIT 5""",
        (user_id, now.year, now.month)
    )

    # Цели пользователя
    goals = await fetchall(
        "SELECT goal_text FROM user_goals WHERE user_id = %s ORDER BY updated_at DESC LIMIT 5",
        (user_id,)
    )
    goals_str = "\n".join(["- " + g[0] for g in goals]) if goals else "не заданы"

    top_str = "\n".join(["- " + r[0] + ": " + "{:,.0f}".format(float(r[1])) + " руб." for r in top]) if top else "нет данных"

    return (
        "КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:\n"
        "Дата: " + now.strftime("%d.%m.%Y") + "\n\n"
        "ТЕКУЩИЙ МЕСЯЦ (" + str(now.month) + "/" + str(now.year) + "):\n"
        "Доходы: " + "{:,.0f}".format(summary['income']) + " руб.\n"
        "Расходы: " + "{:,.0f}".format(summary['total_expense']) + " руб.\n"
        "Баланс: " + "{:,.0f}".format(summary['balance']) + " руб.\n\n"
        "ПРОШЛЫЙ МЕСЯЦ:\n"
        "Доходы: " + "{:,.0f}".format(prev['income']) + " руб.\n"
        "Расходы: " + "{:,.0f}".format(prev['total_expense']) + " руб.\n\n"
        "ТОП РАСХОДОВ:\n" + top_str + "\n\n"
        "КАТЕГОРИИ: " + cat_list + "\n\n"
        "ЦЕЛИ ПОЛЬЗОВАТЕЛЯ:\n" + goals_str
    )


async def get_ai_response(user_id: int, user_message: str, history: list) -> tuple[str, list]:
    import os

    context = await get_user_context(user_id)

    system_prompt = (
        "Ты Баланс — персональный финансовый ИИ-советник. "
        "Ты помогаешь только с финансовыми вопросами: учёт доходов и расходов, "
        "анализ трат, планирование бюджета, выход из финансового кризиса, "
        "накопления, кредиты, инвестиции. "
        "Если вопрос не связан с финансами — вежливо откажи и верни разговор к финансам.\n\n"
        "Ты умеешь:\n"
        "- Вносить транзакции по запросу пользователя\n"
        "- Анализировать расходы и доходы\n"
        "- Составлять финансовые планы\n"
        "- Давать конкретные рекомендации с цифрами\n"
        "- Запоминать цели пользователя\n\n"
        "ПРАВИЛА ОТВЕТОВ:\n"
        "- Отвечай кратко и конкретно\n"
        "- Используй цифры из контекста\n"
        "- Давай actionable советы\n"
        "- Не повторяй контекст пользователю дословно\n\n"
        "ДЕЙСТВИЯ (добавляй в конец ответа если нужно):\n"
        "Для внесения транзакции:\n"
        "TRANSACTION: -1500 Еда/Продукты нал\n"
        "Для сохранения цели:\n"
        "GOAL: накопить 200000 к августу 2026\n"
        "Можно несколько действий сразу. Если действий нет — не пиши эти строки.\n\n"
        + context
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages += history[-10:]
    messages += [{"role": "user", "content": user_message}]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 1000,
                "messages": messages,
            },
            timeout=30.0
        )
        data = response.json()

    ai_text = data["choices"][0]["message"]["content"]
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

    limits = {'start': 100, 'premium': 9999, 'business': 9999}
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
        "INSERT INTO ai_usage (user_id, usage_type, used_at, month_year) VALUES (%s, 'chat', NOW(), TO_CHAR(NOW(), 'YYYY-MM'))",
        (user_id,)
    )


async def process_actions(user_id: int, ai_text: str) -> tuple[str, str]:
    from app.parser import parse_quick_input
    from app.database import get_categories, add_transaction, execute
    actions_log = ""
    clean_lines = []

    for line in ai_text.split("\n"):
        if line.startswith("TRANSACTION:"):
            tx_str = line.replace("TRANSACTION:", "").strip()
            try:
                categories = await get_categories(user_id)
                parsed = parse_quick_input(tx_str)
                if parsed and parsed.get('amount'):
                    hint = parsed.get('category_hint', '')
                    type_ = parsed.get('type', 'expense')
                    category_id = None
                    for cat in categories:
                        if hint and hint.lower() in cat['name'].lower():
                            category_id = cat['id']
                            type_ = cat.get('type', type_)
                            break
                    if not category_id:
                        for cat in categories:
                            if cat.get('type') == type_:
                                category_id = cat['id']
                                break
                    if category_id:
                        await add_transaction(
                            user_id,
                            category_id=category_id,
                            amount=parsed['amount'],
                            type_=type_,
                            kind=parsed.get('kind', 'variable'),
                            comment=parsed.get('comment', '')
                        )
                        actions_log += "\nТранзакция внесена!"
            except Exception as e:
                actions_log += "\nОшибка внесения транзакции: " + str(e)
        elif line.startswith("GOAL:"):
            goal_text = line.replace("GOAL:", "").strip()
            try:
                await execute(
                    "INSERT INTO user_goals (user_id, goal_text) VALUES (%s, %s)",
                    (user_id, goal_text)
                )
                actions_log += "\nЦель сохранена!"
            except Exception as e:
                actions_log += "\nОшибка сохранения цели: " + str(e)
        else:
            clean_lines.append(line)

    clean_text = "\n".join(clean_lines).strip()
    return clean_text, actions_log


@router.callback_query(F.data == "ai_assistant")
async def cb_ai_assistant(call: CallbackQuery, state: FSMContext):
    from app.database import get_user_tier
    tier = await get_user_tier(call.from_user.id)
    if tier == 'free':
        await call.message.edit_text(
            "ИИ-ассистент доступен с тарифа Старт (149 руб/мес).",
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
        "Баланс — финансовый советник\n\n"
        "Могу помочь:\n"
        "- Внести транзакцию ('потратил 3500 на продукты')\n"
        "- Проанализировать расходы\n"
        "- Составить план выхода из минуса\n"
        "- Сохранить финансовую цель\n"
        "- Ответить на вопросы по финансам\n\n"
        "Сообщений: " + limit_str,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
        ])
    )


@router.callback_query(F.data == "ai_end")
async def cb_ai_end(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "Чат завершён.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(AIState.chatting, F.voice)
async def msg_ai_voice(message: Message, state: FSMContext):
    """Голосовое сообщение в ИИ-ассистенте."""
    import httpx, os
    from app.handlers.voice import transcribe_voice
    thinking = await message.answer("Распознаю голос...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        audio_bytes = file_bytes.read()
        text = await transcribe_voice(audio_bytes)
        if not text:
            await thinking.edit_text("Не удалось распознать голос.")
            return
        await thinking.edit_text("Распознано: " + text)
        # Обрабатываем напрямую как ИИ-запрос
        from app.database import get_ai_history, save_ai_message
        history = await get_ai_history(message.from_user.id)
        ai_text, new_history = await get_ai_response(message.from_user.id, text, history)
        await save_ai_message(message.from_user.id, 'user', text)
        await save_ai_message(message.from_user.id, 'assistant', ai_text)
        await log_ai_usage(message.from_user.id)
        clean_text, actions_log = await process_actions(message.from_user.id, ai_text)
        await message.answer(
            clean_text + actions_log,
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
            ])
        )
    except Exception as e:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.answer("Ошибка: " + str(e))


@router.message(AIState.chatting)
async def msg_ai_chat(message: Message, state: FSMContext):
    can, used, limit = await check_ai_limit(message.from_user.id)
    if not can:
        await state.clear()
        await message.answer(
            "Лимит ИИ-ассистента исчерпан.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    data = await state.get_data()
    history = data.get('history', [])

    thinking_msg = await message.answer("Думаю...", parse_mode=None)

    try:
        user_text = message.text or message.caption or ""
        ai_text, new_history = await get_ai_response(
            message.from_user.id, user_text, history
        )
        await log_ai_usage(message.from_user.id)
    except Exception as e:
        await thinking_msg.delete()
        await message.answer("Ошибка: " + str(e))
        return

    await state.update_data(history=new_history[-20:])
    clean_text, actions_log = await process_actions(message.from_user.id, ai_text)

    await thinking_msg.delete()

    limit_str = "безлимит" if limit >= 9999 else str(used + 1) + "/" + str(limit)
    await message.answer(
        clean_text + actions_log + "\n\n[" + limit_str + "]",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
        ])
    )
