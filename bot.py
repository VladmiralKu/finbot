import asyncio
import logging
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers.admin import router as admin_router
from app.handlers.main import router as main_router
from app.handlers.business import router as business_router
from app.handlers.ai_assistant import router as ai_router
from app.handlers.receipt import router as receipt_router
from app.handlers.bank_import import router as bank_import_router
from app.handlers.voice import router as voice_router
from app.handlers.recurring import router as recurring_router
from app.handlers.premium import router as premium_router
from app.handlers.credits import router as credits_router
from app.database import get_pool, close_pool
from app.scheduler import setup_scheduler
from app.middleware import MaintenanceMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware техобслуживания
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())

    dp.include_router(admin_router)
    dp.include_router(credits_router)
    dp.include_router(main_router)
    dp.include_router(business_router)
    dp.include_router(ai_router)
    dp.include_router(receipt_router)
    dp.include_router(bank_import_router)
    dp.include_router(voice_router)
    dp.include_router(recurring_router)
    dp.include_router(premium_router)

    await get_pool()
    logger.info("Database pool initialized")
    await bot.set_my_commands([
        BotCommand(command="menu", description="меню"),
        BotCommand(command="ctrl_z", description="↩️ CTRL+Z"),
        BotCommand(command="help", description="помощь"),
        BotCommand(command="category", description="категории"),
        BotCommand(command="ai", description="ИИ-помощник"),
    ])

    # Лёгкие безопасные миграции
    try:
        from app.database import execute
        await execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS import_hash VARCHAR(64)")
        await execute("CREATE INDEX IF NOT EXISTS idx_transactions_import_hash ON transactions(user_id, import_hash)")
    except Exception as e:
        logger.warning("Migration import_hash: " + str(e))

    try:
        from app.database import execute
        await execute("ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_voice_input BOOLEAN DEFAULT FALSE")
        await execute("ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_annual_plan BOOLEAN DEFAULT FALSE")
        await execute("ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_dds_categories BOOLEAN DEFAULT FALSE")
        await execute("ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_export BOOLEAN DEFAULT FALSE")
        await execute("""
            UPDATE tier_limits
            SET has_voice_input = tier IN ('base', 'premium', 'business'),
                has_export = tier IN ('premium', 'business'),
                has_dds_categories = tier IN ('premium', 'business'),
                has_annual_plan = tier IN ('premium', 'business')
        """)
    except Exception as e:
        logger.warning("Migration tier flags: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS ai_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("CREATE INDEX IF NOT EXISTS idx_ai_history_user ON ai_history(user_id, created_at)")
    except Exception as e:
        logger.warning("Migration ai_history: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    except Exception as e:
        logger.warning("Migration bot_settings: " + str(e))

    try:
        from app.database import execute
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_until TIMESTAMP")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_video_sent_at TIMESTAMP")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_subscription_tier VARCHAR(50)")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_subscription_until TIMESTAMP")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_subscription_is_paid BOOLEAN DEFAULT FALSE")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_started_at TIMESTAMP")
        await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_until TIMESTAMP")
        await execute("""
            UPDATE users
            SET paid_until = premium_until
            WHERE paid_until IS NULL
              AND premium_until IS NOT NULL
              AND COALESCE(subscription_tier, 'free') <> 'free'
              AND premium_until::date > created_at::date + 10
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS paid_expiry_reminders (
                user_id BIGINT NOT NULL,
                paid_until_date DATE NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, paid_until_date)
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS subscription_bonus_messages (
                user_id BIGINT NOT NULL,
                bonus_start_date DATE NOT NULL,
                day INT NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, bonus_start_date, day)
            )
        """)
    except Exception as e:
        logger.warning("Migration subscription lifecycle: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS trial_journey_messages (
                user_id BIGINT NOT NULL,
                day INT NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, day)
            )
        """)
    except Exception as e:
        logger.warning("Migration trial journey: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS daily_activity_reminders (
                user_id BIGINT NOT NULL,
                reminder_date DATE NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, reminder_date)
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS weekly_reports (
                user_id BIGINT NOT NULL,
                week_start DATE NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, week_start)
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS user_goals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                goal_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("ALTER TABLE user_goals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
        await execute("CREATE INDEX IF NOT EXISTS idx_user_goals_user ON user_goals(user_id, updated_at)")
    except Exception as e:
        logger.warning("Migration engagement tables: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS credit_cards (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(128) NOT NULL,
                debt_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
                credit_limit NUMERIC(12, 2),
                min_payment NUMERIC(12, 2),
                payment_day INT,
                interest_rate NUMERIC(6, 2),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS credit_card_events (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                card_id INT NOT NULL REFERENCES credit_cards(id) ON DELETE CASCADE,
                event_type VARCHAR(32) NOT NULL,
                amount NUMERIC(12, 2),
                debt_amount NUMERIC(12, 2),
                credit_limit NUMERIC(12, 2),
                comment TEXT,
                event_date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS credit_balance_requests (
                user_id BIGINT NOT NULL,
                request_month DATE NOT NULL,
                sent_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, request_month)
            )
        """)
        await execute("CREATE INDEX IF NOT EXISTS idx_credit_cards_user ON credit_cards(user_id, is_active)")
        await execute("CREATE INDEX IF NOT EXISTS idx_credit_card_events_card ON credit_card_events(user_id, card_id, event_date)")
    except Exception as e:
        logger.warning("Migration credit cards: " + str(e))

    try:
        from app.database import execute
        await execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await execute("""
            CREATE TABLE IF NOT EXISTS undo_actions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                entity_type VARCHAR(64) NOT NULL,
                entity_id BIGINT NOT NULL,
                action_type VARCHAR(16) NOT NULL,
                before_data JSONB,
                after_data JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                undone_at TIMESTAMP
            )
        """)
        await execute("CREATE INDEX IF NOT EXISTS idx_undo_actions_user ON undo_actions(user_id, undone_at, created_at DESC, id DESC)")
        await execute("""
            CREATE OR REPLACE FUNCTION log_undo_action()
            RETURNS trigger AS $$
            BEGIN
                IF current_setting('app.undo_disabled', true) = '1' THEN
                    IF TG_OP = 'DELETE' THEN
                        RETURN OLD;
                    END IF;
                    RETURN NEW;
                END IF;

                IF TG_OP = 'UPDATE' AND to_jsonb(NEW) = to_jsonb(OLD) THEN
                    RETURN NEW;
                END IF;

                IF TG_ARGV[0] = 'recurring_payment' AND TG_OP = 'UPDATE' THEN
                    IF (to_jsonb(NEW) - 'last_triggered_at' - 'next_trigger_date')
                       = (to_jsonb(OLD) - 'last_triggered_at' - 'next_trigger_date') THEN
                        RETURN NEW;
                    END IF;
                END IF;

                IF TG_OP = 'INSERT' THEN
                    INSERT INTO undo_actions (user_id, entity_type, entity_id, action_type, after_data)
                    VALUES (NEW.user_id, TG_ARGV[0], NEW.id, lower(TG_OP), to_jsonb(NEW));
                    RETURN NEW;
                ELSIF TG_OP = 'UPDATE' THEN
                    INSERT INTO undo_actions (user_id, entity_type, entity_id, action_type, before_data, after_data)
                    VALUES (NEW.user_id, TG_ARGV[0], NEW.id, lower(TG_OP), to_jsonb(OLD), to_jsonb(NEW));
                    RETURN NEW;
                ELSE
                    INSERT INTO undo_actions (user_id, entity_type, entity_id, action_type, before_data)
                    VALUES (OLD.user_id, TG_ARGV[0], OLD.id, lower(TG_OP), to_jsonb(OLD));
                    RETURN OLD;
                END IF;
            END;
            $$ LANGUAGE plpgsql;
        """)
        undo_triggers = [
            ("transactions", "transaction"),
            ("notes", "note"),
            ("recurring_payments", "recurring_payment"),
            ("user_goals", "user_goal"),
            ("credit_cards", "credit_card"),
        ]
        for table, entity in undo_triggers:
            await execute(f"""
                DO $$
                BEGIN
                    IF to_regclass('public.{table}') IS NOT NULL THEN
                        EXECUTE 'DROP TRIGGER IF EXISTS trg_undo_{table} ON {table}';
                        EXECUTE 'CREATE TRIGGER trg_undo_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION log_undo_action(''{entity}'')';
                    END IF;
                END $$;
            """)
    except Exception as e:
        logger.warning("Migration undo actions: " + str(e))

    scheduler = setup_scheduler(bot)

    from app.webhook_server import start_webhook_server
    await start_webhook_server(bot)

    try:
        logger.info("Bot started")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await close_pool()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
