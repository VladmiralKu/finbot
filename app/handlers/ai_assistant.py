from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from html import escape
import re
import httpx

router = Router()

AI_MODE_TALK = "talk"
AI_MODE_ACTION = "action"
AI_MODE_PROFILE = "profile"


def _ai_mode_keyboard(mode: str) -> InlineKeyboardMarkup:
    talk_text = "✓ Разговор" if mode in (AI_MODE_TALK, AI_MODE_PROFILE) else "Разговор"
    action_text = "✓ Действие" if mode == AI_MODE_ACTION else "Действие"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=talk_text, callback_data="ai_mode:" + AI_MODE_TALK),
            InlineKeyboardButton(text=action_text, callback_data="ai_mode:" + AI_MODE_ACTION),
        ],
        [InlineKeyboardButton(text="Завершить", callback_data="ai_end")],
    ])


class AIState(StatesGroup):
    chatting = State()


def _draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Внести", callback_data="ai_draft_commit")],
        [InlineKeyboardButton(text="✏️ Уточнить", callback_data="ai_draft_clarify")],
        [InlineKeyboardButton(text="Отмена", callback_data="ai_draft_cancel")],
    ])


def _format_draft_amount(tx: dict) -> str:
    sign = "-" if tx.get("type") == "expense" else "+"
    return sign + f"{float(tx.get('amount') or 0):,.0f}".replace(",", " ") + " ₽"


def _format_draft_text(transactions: list[dict], intro: str | None = None) -> str:
    lines = [intro or "Разобрал сообщение:"]
    lines.append("")
    for index, tx in enumerate(transactions, start=1):
        tx_date = tx.get("transaction_date")
        if hasattr(tx_date, "strftime"):
            date_text = tx_date.strftime("%d.%m.%Y")
        else:
            date_text = str(tx_date or "")
        line = (
            str(index) + ". <b>" + escape(date_text) + "</b> "
            + "<b>" + escape(_format_draft_amount(tx)) + "</b>"
            + " — " + escape(str(tx.get("category_name") or "Без категории"))
        )
        comment = (tx.get("comment") or "").strip()
        if comment:
            line += "\n   💬 <i>«" + escape(comment) + "»</i>"
        lines.append(line)
    lines.append("")
    lines.append("Сейчас такие операции вносятся сразу. Этот экран нужен только для старых черновиков.")
    return "\n".join(lines)


def _looks_like_complex_transaction_draft(text: str) -> bool:
    lower = (text or "").lower()
    without_dates = re.sub(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", " ", lower)
    without_dates = re.sub(
        r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-яё]*",
        " ",
        without_dates,
    )
    amounts = re.findall(r"(?<!\d)[+-]?\d[\d\s]*(?:[,.]\d{1,2})?(?!\d)", without_dates)
    if len(amounts) < 2:
        return False
    date_like = any(word in lower for word in (
        "сегодня", "вчера", "позавчера", "завтра", "дня назад", "дней назад",
    ))
    date_like = date_like or bool(
        re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", lower)
        or re.search(
            r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
            lower,
        )
    )
    separator_like = "\n" in text or ";" in text or "," in text or " потом " in lower or " затем " in lower
    return date_like or separator_like


async def _build_history_lookup_context(user_id: int, user_message: str | None) -> str:
    from app.services.transaction_lookup import find_transaction_mentions

    query, rows = await find_transaction_mentions(user_id, user_message, limit=10)
    if not query:
        return ""

    if not rows:
        return (
            "\n\nПОИСК ПО ВСЕЙ БАЗЕ ПОЛЬЗОВАТЕЛЯ:\n"
            "Запрос: " + query + "\n"
            "Совпадений в транзакциях не найдено."
        )

    lines = []
    for row in rows:
        sign = "-" if row[3] == "expense" else "+"
        comment = (" | " + row[5]) if row[5] else ""
        lines.append(
            row[1].strftime("%d.%m.%Y") + " "
            + sign + "{:,.0f}".format(float(row[2])) + " "
            + str(row[4] or "") + comment
        )

    return (
        "\n\nПОИСК ПО ВСЕЙ БАЗЕ ПОЛЬЗОВАТЕЛЯ:\n"
        "Запрос: " + query + "\n"
        "Последние совпадения:\n" + "\n".join(lines)
    )


async def _send_transaction_draft(
    target,
    state: FSMContext,
    user_id: int,
    user_text: str,
    previous_draft: list[dict] | None = None,
    source_text: str | None = None,
    keep_ai_state: bool = True,
):
    from app.services.transaction_drafts import (
        build_transaction_draft,
    )
    from app.services.onboarding_video import maybe_send_onboarding_video
    from app.services.transaction_entry import save_transactions_and_build_response

    can, used, limit = await check_ai_limit(user_id)
    if not can:
        await state.clear()
        await target.answer(
            "Лимит ИИ-помощника исчерпан или тариф не даёт доступ.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return

    thinking = await target.answer("Разбираю сообщение и сразу вношу операции...")
    transactions = await build_transaction_draft(user_id, user_text, previous_draft=previous_draft)
    await log_ai_usage(user_id)
    if not transactions:
        if keep_ai_state:
            await state.set_state(AIState.chatting)
            await state.update_data(
                ai_mode=AI_MODE_ACTION,
                history=[],
                ai_draft_source_text=source_text or user_text,
                pending_transaction_draft=[],
                ai_draft_waiting_clarification=False,
            )
            reply_markup = _ai_mode_keyboard(AI_MODE_ACTION)
        else:
            await state.clear()
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="ИИ-помощник", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        await thinking.edit_text(
            "Не нашёл в сообщении операции, которые можно уверенно внести.\n\n"
            "Можно уточнить прямо здесь, например: «вчера 500 кофе, сегодня 1200 продукты».",
            reply_markup=reply_markup,
        )
        return

    saved_ids, response_text = await save_transactions_and_build_response(user_id, transactions)
    if keep_ai_state:
        await state.set_state(AIState.chatting)
        await state.update_data(
            ai_mode=AI_MODE_ACTION,
            history=[],
            ai_draft_source_text=source_text or user_text,
            pending_transaction_draft=[],
            ai_draft_waiting_clarification=False,
        )
        reply_markup = _ai_mode_keyboard(AI_MODE_ACTION)
    else:
        await state.clear()
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Открыть список", callback_data="recent")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    await thinking.edit_text(
        response_text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if saved_ids:
        await maybe_send_onboarding_video(target.bot, user_id)


async def get_user_context(user_id: int, user_message: str | None = None) -> str:
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
        "SELECT id, goal_text FROM user_goals WHERE user_id = %s ORDER BY updated_at DESC LIMIT 5",
        (user_id,)
    )
    goals_str = "\n".join(["- #" + str(g[0]) + " " + g[1] for g in goals]) if goals else "не заданы"

    # Последние 20 транзакций с комментариями
    recent_txs = await fetchall(
        """SELECT t.transaction_date, t.amount, t.type, c.name, t.comment
           FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s
           ORDER BY t.transaction_date DESC, t.created_at DESC
           LIMIT 20""",
        (user_id,)
    )
    tx_lines = []
    for tx in recent_txs:
        sign = "-" if tx[2] == "expense" else "+"
        comment = (" | " + tx[4]) if tx[4] else ""
        tx_lines.append(
            tx[0].strftime("%d.%m") + " " + sign + "{:,.0f}".format(float(tx[1])) +
            " " + (tx[3] or "") + comment
        )
    txs_str = "\n".join(tx_lines) if tx_lines else "нет транзакций"

    # Заметки пользователя
    notes = await fetchall(
        "SELECT text, created_at FROM notes WHERE user_id = %s ORDER BY created_at DESC LIMIT 10",
        (user_id,)
    )
    notes_str = "\n".join(["- " + n[0] for n in notes]) if notes else "нет заметок"

    top_str = "\n".join(["- " + r[0] + ": " + "{:,.0f}".format(float(r[1])) + " руб." for r in top]) if top else "нет данных"
    history_lookup = await _build_history_lookup_context(user_id, user_message)

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
        "ПОСЛЕДНИЕ ТРАНЗАКЦИИ:\n" + txs_str + "\n\n"
        "КАТЕГОРИИ: " + cat_list + "\n\n"
        "ЦЕЛИ ПОЛЬЗОВАТЕЛЯ:\n" + goals_str + "\n\n"
        "ЗАМЕТКИ:\n" + notes_str
        + history_lookup
    )


async def get_ai_response(
    user_id: int,
    user_message: str,
    history: list,
    tier: str = None,
    mode: str = AI_MODE_TALK,
) -> tuple[str, list]:
    import os

    context = await get_user_context(user_id, user_message)

    if mode == AI_MODE_ACTION:
        mode_prompt = (
            "РЕЖИМ: Действие. Пользователь ожидает, что ты поможешь привести разговор к конкретному действию внутри бота. "
            "Можно выполнять реальные действия только через специальные строки в конце ответа.\n\n"
            "ДЕЙСТВИЯ (добавляй в конец ответа только если нужен реальный action внутри бота):\n"
            "Для внесения транзакции:\n"
            "TRANSACTION: -1500 Еда/Продукты\n"
            "Для сохранения или обновления финансовой цели/описания пользователя:\n"
            "GOAL: хочу выйти из минуса, снизить импульсивные траты и накопить резерв\n"
            "Для удаления неактуальной цели:\n"
            "DELETE_GOAL: #12\n"
            "Если номера нет, можно написать текст цели: DELETE_GOAL: накопить резерв\n"
            "Если просьба неоднозначная, сначала задай короткий уточняющий вопрос и не выполняй действие.\n\n"
        )
    elif mode == AI_MODE_PROFILE:
        mode_prompt = (
            "РЕЖИМ: Знакомство. Пользователь только начинает работу и может рассказать о себе, доходах, проблемах, "
            "страхах, привычках и финансовой цели. Помоги ему сформулировать главное финансовое описание. "
            "Можно сохранять только GOAL, не выполняй транзакции, не меняй категории, не удаляй данные.\n\n"
            "Если пользователь рассказал о себе или цели, добавь в конец ответа служебную строку:\n"
            "GOAL: краткое описание цели, контекста и финансовой проблемы пользователя\n"
            "Если информации мало, задай один тёплый уточняющий вопрос и не добавляй GOAL.\n\n"
            "Стиль в знакомстве: коротко, живо и дерзковато, как Duolingo. Можно мягко поддразнивать, "
            "но без грубости, стыда и канцелярита.\n\n"
        )
    else:
        mode_prompt = (
            "РЕЖИМ: Разговор. Это безопасный режим для обсуждения, анализа и развития мысли. "
            "Ты можешь использовать данные пользователя как контекст, но НЕ меняй ничего в боте. "
            "Не добавляй, не удаляй и не редактируй транзакции, категории, цели, заметки или платежи. "
            "Никогда не выводи служебные строки TRANSACTION, GOAL или DELETE_GOAL в этом режиме. "
            "Если пользователь просит выполнить действие, предложи переключиться в режим «Действие».\n\n"
        )

    system_prompt = (
        "Ты Баланс — персональный ассистент в финансовом приложении. "
        "Пользователь на платном тарифе, поэтому можешь свободно общаться на любые темы, "
        "не только финансовые — отвечай как обычный полезный собеседник. "
        "Ты работаешь только в текстовом формате: НЕ обещай генерировать файлы, аудио или видео. "
        "Если пользователь просит красивый финансовый отчёт картинкой, скажи, что это можно запустить отдельной командой: «сделай красивый отчёт за месяц».\n\n"
        "При этом ты хорошо разбираешься в финансах пользователя и можешь:\n"
        "- Вносить транзакции по запросу пользователя\n"
        "- Анализировать расходы и доходы\n"
        "- Составлять финансовые планы\n"
        "- Давать конкретные рекомендации с цифрами\n"
        "- Запоминать цели пользователя\n\n"
        + mode_prompt +
        "ПРАВИЛА ОТВЕТОВ:\n"
        "- Отвечай по существу вопроса, кратко если возможно\n"
        "- Если вопрос финансовый — используй цифры из контекста пользователя\n"
        "- Не повторяй контекст пользователю дословно\n\n"
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

    limits = {'scan_text': 60, 'base': 150, 'premium': 9999, 'business': 9999}
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


def _normalize_goal_text(text: str) -> str:
    return " ".join((text or "").lower().replace("#", "").split())


async def delete_goal_by_query(user_id: int, query: str) -> tuple[bool, str]:
    from app.database import execute, fetchall

    clean_query = _normalize_goal_text(query)
    if not clean_query:
        return False, "Не понял, какую цель удалить."

    if clean_query.isdigit():
        row_count = await execute(
            "DELETE FROM user_goals WHERE user_id = %s AND id = %s",
            (user_id, int(clean_query)),
        )
        if row_count:
            return True, "Цель удалена!"
        return False, "Не нашёл цель с таким номером."

    goals = await fetchall(
        "SELECT id, goal_text FROM user_goals WHERE user_id = %s ORDER BY updated_at DESC",
        (user_id,),
    )
    matches = []
    for goal_id, goal_text in goals:
        normalized_goal = _normalize_goal_text(goal_text)
        if clean_query == normalized_goal or clean_query in normalized_goal or normalized_goal in clean_query:
            matches.append((goal_id, goal_text))

    if not matches:
        return False, "Не нашёл такую цель. Можно написать её точнее или указать номер из списка целей."

    if len(matches) > 1:
        variants = ", ".join(["#" + str(goal_id) for goal_id, _ in matches[:5]])
        return False, "Нашёл несколько похожих целей: " + variants + ". Напиши, какую удалить по номеру."

    goal_id, goal_text = matches[0]
    await execute(
        "DELETE FROM user_goals WHERE user_id = %s AND id = %s",
        (user_id, goal_id),
    )
    return True, "Цель удалена: " + goal_text


def _user_requested_transaction_action(user_message: str) -> bool:
    lower = (user_message or "").lower()
    lookup_markers = (
        "найди", "найти", "покажи", "показать", "посмотри", "посмотреть",
        "сколько", "какие", "какой", "какая", "где", "когда", "отчет", "отчёт",
        "анализ", "проанализ", "с копейками",
    )
    if any(marker in lower for marker in lookup_markers):
        return False
    money_like = re.search(r"\d[\d\s]*(?:[,.]\d+)?\s*(?:р|руб|рубл|₽)?", lower)
    action_markers = (
        "внеси", "внести", "запиши", "записать", "добавь", "добавить",
        "потратил", "потратила", "потрачено", "купил", "купила", "оплатил",
        "оплатила", "заработал", "заработала", "получил", "получила",
        "доход", "расход",
    )
    return any(marker in lower for marker in action_markers) or bool(money_like)


async def process_actions(
    user_id: int,
    ai_text: str,
    user_message: str = "",
    allow_actions: bool = True,
    allow_transactions: bool = True,
    allow_goals: bool = True,
) -> tuple[str, str]:
    from app.database import execute
    from app.services.insights import build_transaction_insight
    from app.services.transaction_ai import extract_transactions_from_text
    from app.services.transaction_service import create_transaction
    actions_log = ""
    clean_lines = []
    insight_added = False

    for line in ai_text.split("\n"):
        action_line = line.strip()
        if action_line.startswith("TRANSACTION:"):
            tx_str = action_line.replace("TRANSACTION:", "").strip()
            if not allow_actions or not allow_transactions or not _user_requested_transaction_action(user_message):
                continue
            try:
                transactions = await extract_transactions_from_text(user_id, tx_str, source="ai")
                for tx in transactions:
                    saved = await create_transaction(
                        user_id=user_id,
                        category_id=tx["category_id"],
                        amount=tx["amount"],
                        type_=tx["type"],
                        kind=tx.get("kind"),
                        comment=tx.get("comment") or "",
                        transaction_date=tx.get("transaction_date"),
                        pnl_period=tx.get("pnl_period"),
                    )
                    actions_log += "\nТранзакция внесена!"
                    if not insight_added:
                        insight = await build_transaction_insight(user_id, saved["id"])
                        if insight:
                            actions_log += "\n" + insight
                            insight_added = True
            except Exception as e:
                actions_log += "\nОшибка внесения транзакции: " + str(e)
        elif action_line.startswith("GOAL:"):
            goal_text = action_line.replace("GOAL:", "").strip()
            if not allow_actions or not allow_goals:
                continue
            if not goal_text:
                continue
            try:
                await execute("DELETE FROM user_goals WHERE user_id = %s", (user_id,))
                await execute(
                    "INSERT INTO user_goals (user_id, goal_text) VALUES (%s, %s)",
                    (user_id, goal_text)
                )
                actions_log += "\nЦель зафиксировал. Теперь деньгам сложнее делать вид, что их не спрашивали."
            except Exception as e:
                actions_log += "\nОшибка сохранения цели: " + str(e)
        elif action_line.startswith("DELETE_GOAL:"):
            goal_query = action_line.replace("DELETE_GOAL:", "").strip()
            if not allow_actions or not allow_goals:
                continue
            try:
                _, message = await delete_goal_by_query(user_id, goal_query)
                actions_log += "\n" + message
            except Exception as e:
                actions_log += "\nОшибка удаления цели: " + str(e)
        else:
            clean_lines.append(line)

    clean_text = "\n".join(clean_lines).strip()
    return clean_text, actions_log


async def open_ai_assistant(target, state: FSMContext, user_id: int, edit: bool = False):
    from app.database import get_user_tier
    tier = await get_user_tier(user_id)
    if tier == 'free':
        text = "ИИ-помощник доступен с тарифа Старт (149 руб/мес)."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
        if edit:
            await target.edit_text(text, parse_mode=None, reply_markup=kb)
        else:
            await target.answer(text, parse_mode=None, reply_markup=kb)
        return

    can, used, limit = await check_ai_limit(user_id)
    if not can:
        text = "Лимит ИИ-помощника исчерпан на этот месяц.\nИспользовано: " + str(used) + "/" + str(limit)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
        if edit:
            await target.edit_text(text, parse_mode=None, reply_markup=kb)
        else:
            await target.answer(text, parse_mode=None, reply_markup=kb)
        return

    await state.set_state(AIState.chatting)
    await state.update_data(history=[], ai_mode=AI_MODE_TALK)

    limit_str = "безлимит" if limit >= 9999 else str(used) + "/" + str(limit)

    greeting = (
        "Баланс — твой ИИ-помощник\n\n"
        "Сейчас включён режим «Разговор»: можно обсуждать идеи, анализировать цифры и задавать вопросы. "
        "В этом режиме я ничего не меняю в боте.\n\n"
        "Переключись в «Действие», если нужно записать операцию, изменить данные, цель, категорию или запустить отчёт.\n\n"
        "Сообщений: " + limit_str
    )

    kb = _ai_mode_keyboard(AI_MODE_TALK)
    if edit:
        await target.edit_text(greeting, parse_mode=None, reply_markup=kb)
    else:
        await target.answer(greeting, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data == "ai_assistant")
async def cb_ai_assistant(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await open_ai_assistant(call.message, state, call.from_user.id, edit=True)


@router.message(Command("ai"))
async def cmd_ai_assistant(message: Message, state: FSMContext):
    await open_ai_assistant(message, state, message.from_user.id, edit=False)


@router.callback_query(F.data.startswith("ai_mode:"))
async def cb_ai_mode(call: CallbackQuery, state: FSMContext):
    await call.answer()
    mode = call.data.split(":", 1)[1]
    if mode not in (AI_MODE_TALK, AI_MODE_ACTION):
        mode = AI_MODE_TALK
    await state.set_state(AIState.chatting)
    await state.update_data(ai_mode=mode, history=[])
    if mode == AI_MODE_ACTION:
        text = (
            "Режим «Действие» включён.\n\n"
            "Теперь можно писать команды обычными словами: «еда 500», «измени категорию у последней операции», "
            "«сохрани цель накопить 200000»."
        )
    else:
        text = (
            "Режим «Разговор» включён.\n\n"
            "Можно обсуждать, анализировать и развивать мысли. В этом режиме я ничего не меняю в боте."
        )
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(text, parse_mode=None, reply_markup=_ai_mode_keyboard(mode))


@router.callback_query(F.data == "ai_action_pending")
async def cb_ai_action_pending(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    user_text = data.get("ai_pending_text")
    if not user_text:
        await call.message.answer(
            "Не нашёл исходное сообщение. Напиши его ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="ИИ-помощник", callback_data="ai_assistant")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ]),
        )
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _send_transaction_draft(call.message, state, call.from_user.id, user_text, keep_ai_state=False)


@router.callback_query(F.data == "ai_draft_clarify")
async def cb_ai_draft_clarify(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    if not data.get("pending_transaction_draft"):
        await call.message.answer("Черновика уже нет. Напиши сообщение заново.", reply_markup=_ai_mode_keyboard(AI_MODE_ACTION))
        return
    await state.set_state(AIState.chatting)
    await state.update_data(ai_mode=AI_MODE_ACTION, ai_draft_waiting_clarification=True)
    await call.message.answer(
        "Напиши уточнение обычным текстом.\n\n"
        "Например: «такси было 700», «продукты были вчера», «убери кофе».",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="ai_draft_cancel")],
        ]),
    )


@router.callback_query(F.data == "ai_draft_cancel")
async def cb_ai_draft_cancel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(
        pending_transaction_draft=[],
        ai_draft_waiting_clarification=False,
        ai_draft_source_text="",
    )
    await call.message.answer(
        "Ок, не вношу.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            [InlineKeyboardButton(text="ИИ-помощник", callback_data="ai_assistant")],
        ]),
    )


@router.callback_query(F.data == "ai_draft_commit")
async def cb_ai_draft_commit(call: CallbackQuery, state: FSMContext):
    await call.answer()
    from app.services.transaction_drafts import deserialize_draft
    from app.services.transaction_entry import save_transactions_and_build_response
    from app.services.onboarding_video import maybe_send_onboarding_video

    data = await state.get_data()
    transactions = deserialize_draft(data.get("pending_transaction_draft"))
    if not transactions:
        await call.message.answer(
            "Черновика уже нет. Напиши сообщение заново.",
            reply_markup=_ai_mode_keyboard(AI_MODE_ACTION),
        )
        return

    saved_ids, response_text = await save_transactions_and_build_response(call.from_user.id, transactions)

    await state.update_data(
        pending_transaction_draft=[],
        ai_draft_waiting_clarification=False,
        ai_draft_source_text="",
        ai_mode=AI_MODE_ACTION,
    )

    await call.message.answer(
        response_text,
        parse_mode="HTML",
        reply_markup=_ai_mode_keyboard(AI_MODE_ACTION),
    )
    await maybe_send_onboarding_video(call.message.bot, call.from_user.id)


@router.callback_query(F.data == "ai_end")
async def cb_ai_end(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "Чат завершён.",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ])
    )


@router.message(AIState.chatting, F.voice)
async def msg_ai_voice(message: Message, state: FSMContext):
    """Голосовое сообщение в ИИ-помощнике."""
    import httpx, os
    from app.handlers.voice import format_recognized_text, transcribe_voice
    from app.database import get_user_tier

    voice_check_tier = await get_user_tier(message.from_user.id)
    if voice_check_tier == 'scan_text':
        await message.answer(
            "Голосовой ввод недоступен на тарифе Скан и текст. Напиши вопрос текстом или перейди на тариф База.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
            ])
        )
        return

    thinking = await message.answer("Распознаю голос...")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        audio_bytes = file_bytes.read()
        text = await transcribe_voice(audio_bytes)
        if not text:
            await thinking.edit_text("Не удалось распознать голос.")
            return
        await thinking.edit_text(format_recognized_text(text), parse_mode="HTML")
        data = await state.get_data()
        mode = data.get("ai_mode", AI_MODE_TALK)
        if mode == AI_MODE_ACTION:
            from app.handlers.main import handle_intent_message
            handled = await handle_intent_message(message, state, text, source="ai_action")
            if handled:
                return
        history = data.get('history', [])
        from app.database import save_ai_message
        from app.database import get_user_tier
        tier = await get_user_tier(message.from_user.id)
        ai_text, new_history = await get_ai_response(message.from_user.id, text, history, tier=tier, mode=mode)
        await state.update_data(history=new_history[-20:])
        await save_ai_message(message.from_user.id, 'user', text)
        await save_ai_message(message.from_user.id, 'assistant', ai_text)
        await log_ai_usage(message.from_user.id)
        clean_text, actions_log = await process_actions(
            message.from_user.id,
            ai_text,
            text,
            allow_actions=(mode in (AI_MODE_ACTION, AI_MODE_PROFILE)),
            allow_transactions=(mode == AI_MODE_ACTION),
            allow_goals=True,
        )
        from app.keyboards import onboarding_finish_keyboard
        await message.answer(
            "🗣️ " + clean_text + actions_log,
            parse_mode=None,
            reply_markup=onboarding_finish_keyboard() if mode == AI_MODE_PROFILE else _ai_mode_keyboard(mode)
        )
        from app.services.onboarding_video import maybe_send_onboarding_video
        await maybe_send_onboarding_video(message.bot, message.from_user.id)
    except Exception as e:
        await thinking.edit_text("Ошибка: " + str(e))


@router.message(AIState.chatting)
async def msg_ai_chat(message: Message, state: FSMContext):
    can, used, limit = await check_ai_limit(message.from_user.id)
    if not can:
        await state.clear()
        await message.answer(
            "Лимит ИИ-помощника исчерпан.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Тарифы", callback_data="premium")],
                [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
            ])
        )
        return

    data = await state.get_data()
    history = data.get('history', [])
    mode = data.get("ai_mode", AI_MODE_TALK)

    try:
        user_text = message.text or message.caption or ""
        if mode == AI_MODE_ACTION:
            if data.get("ai_draft_waiting_clarification"):
                from app.services.transaction_drafts import deserialize_draft
                previous_draft = deserialize_draft(data.get("pending_transaction_draft"))
                source_text = data.get("ai_draft_source_text") or user_text
                await _send_transaction_draft(
                    message,
                    state,
                    message.from_user.id,
                    user_text,
                    previous_draft=previous_draft,
                    source_text=source_text,
                )
                return
            if _looks_like_complex_transaction_draft(user_text):
                await _send_transaction_draft(message, state, message.from_user.id, user_text)
                return
            from app.handlers.main import handle_intent_message
            handled = await handle_intent_message(message, state, user_text, source="ai_action")
            if handled:
                return
        thinking_msg = await message.answer("Думаю...", parse_mode=None)
        from app.database import get_user_tier
        current_tier = await get_user_tier(message.from_user.id)
        ai_text, new_history = await get_ai_response(
            message.from_user.id, user_text, history, tier=current_tier, mode=mode
        )
        await log_ai_usage(message.from_user.id)
    except Exception as e:
        if "thinking_msg" in locals():
            await thinking_msg.edit_text("Ошибка: " + str(e))
        else:
            await message.answer("Ошибка: " + str(e))
        return

    await state.update_data(history=new_history[-20:])
    clean_text, actions_log = await process_actions(
        message.from_user.id,
        ai_text,
        user_text,
        allow_actions=(mode in (AI_MODE_ACTION, AI_MODE_PROFILE)),
        allow_transactions=(mode == AI_MODE_ACTION),
        allow_goals=True,
    )

    limit_str = "безлимит" if limit >= 9999 else str(used + 1) + "/" + str(limit)
    answer_text = "🗣️ " + clean_text + actions_log
    if mode != AI_MODE_PROFILE:
        answer_text += "\n\n[" + limit_str + "]"
    from app.keyboards import onboarding_finish_keyboard
    await thinking_msg.edit_text(
        answer_text,
        parse_mode=None,
        reply_markup=onboarding_finish_keyboard() if mode == AI_MODE_PROFILE else _ai_mode_keyboard(mode)
    )
    from app.services.onboarding_video import maybe_send_onboarding_video
    await maybe_send_onboarding_video(message.bot, message.from_user.id)
