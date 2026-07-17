from app.database import execute, fetchone, get_bot_setting


async def maybe_send_onboarding_video(bot, user_id: int) -> bool:
    row = await fetchone(
        "SELECT onboarding_video_sent_at FROM users WHERE id=%s",
        (user_id,),
    )
    if row and row[0]:
        return False

    has_action = await fetchone(
        """
        SELECT
            EXISTS(SELECT 1 FROM transactions WHERE user_id=%s LIMIT 1)
            OR EXISTS(SELECT 1 FROM user_goals WHERE user_id=%s LIMIT 1)
        """,
        (user_id, user_id),
    )
    if not has_action or not has_action[0]:
        return False

    video_file_id = await get_bot_setting("onboarding_video_file_id")
    if not video_file_id:
        return False

    caption = await get_bot_setting("onboarding_video_caption")
    caption = (caption or "").strip() or None
    if caption and len(caption) > 1024:
        caption = caption[:1021] + "..."

    try:
        await bot.send_video(
            chat_id=user_id,
            video=video_file_id,
            caption=caption,
            supports_streaming=True,
            parse_mode=None,
        )
    except Exception:
        return False

    await execute(
        "UPDATE users SET onboarding_video_sent_at=NOW() WHERE id=%s",
        (user_id,),
    )
    return True
