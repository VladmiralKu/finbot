from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

router = Router()

ADMIN_ID = 388622523


class BroadcastForwardState(StatesGroup):
    waiting_post = State()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    from app.database import fetchall, fetchone

    total_row = await fetchone("SELECT COUNT(*) FROM users")
    total = total_row[0] if total_row else 0

    tier_rows = await fetchall(
        "SELECT subscription_tier, COUNT(*) FROM users GROUP BY subscription_tier ORDER BY COUNT(*) DESC"
    )

    text = "📊 Статистика пользователей\n\n"
    text += "Всего: " + str(total) + "\n\n"
    text += "По тарифам:\n"
    for tier, count in tier_rows:
        text += "  " + str(tier) + ": " + str(count) + "\n"

    await message.answer(text, parse_mode=None)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip()[len("/broadcast"):].strip()
    if not text:
        await message.answer(
            "Формат: /broadcast Текст сообщения\n\n"
            "Сообщение будет отправлено всем пользователям бота."
        )
        return

    from app.database import fetchall
    users = await fetchall("SELECT id FROM users")

    sent = 0
    failed = 0
    status_msg = await message.answer("Рассылка началась... 0/" + str(len(users)))

    for i, (user_id,) in enumerate(users):
        try:
            await message.bot.send_message(user_id, text, parse_mode=None)
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    "Рассылка идёт... " + str(i + 1) + "/" + str(len(users))
                )
            except Exception:
                pass

    await status_msg.edit_text(
        "Рассылка завершена!\nОтправлено: " + str(sent) + "\nНе удалось: " + str(failed)
    )



@router.message(Command("broadcast_forward"))
async def cmd_broadcast_forward(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastForwardState.waiting_post)
    await message.answer(
        "Пришли пост (текст, фото, видео — что угодно), который нужно разослать всем пользователям.\n\n"
        "Можно переслать сообщение из любого канала."
    )


@router.message(BroadcastForwardState.waiting_post)
async def msg_broadcast_forward_post(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()

    from app.database import fetchall
    users = await fetchall("SELECT id FROM users")

    sent = 0
    failed = 0
    status_msg = await message.answer("Рассылка началась... 0/" + str(len(users)))

    for i, (user_id,) in enumerate(users):
        try:
            await message.bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    "Рассылка идёт... " + str(i + 1) + "/" + str(len(users))
                )
            except Exception:
                pass

    await status_msg.edit_text(
        "Рассылка завершена!\nОтправлено: " + str(sent) + "\nНе удалось: " + str(failed)
    )
