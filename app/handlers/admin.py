from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

router = Router()

ADMIN_ID = 388622523


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


class BroadcastState:
    waiting_text = {}


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
