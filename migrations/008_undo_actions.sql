CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

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
);

CREATE INDEX IF NOT EXISTS idx_undo_actions_user
    ON undo_actions(user_id, undone_at, created_at DESC, id DESC);

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

DROP TRIGGER IF EXISTS trg_undo_transactions ON transactions;
CREATE TRIGGER trg_undo_transactions
AFTER INSERT OR UPDATE OR DELETE ON transactions
FOR EACH ROW EXECUTE FUNCTION log_undo_action('transaction');

DROP TRIGGER IF EXISTS trg_undo_notes ON notes;
CREATE TRIGGER trg_undo_notes
AFTER INSERT OR UPDATE OR DELETE ON notes
FOR EACH ROW EXECUTE FUNCTION log_undo_action('note');

DROP TRIGGER IF EXISTS trg_undo_recurring_payments ON recurring_payments;
CREATE TRIGGER trg_undo_recurring_payments
AFTER INSERT OR UPDATE OR DELETE ON recurring_payments
FOR EACH ROW EXECUTE FUNCTION log_undo_action('recurring_payment');

DROP TRIGGER IF EXISTS trg_undo_user_goals ON user_goals;
CREATE TRIGGER trg_undo_user_goals
AFTER INSERT OR UPDATE OR DELETE ON user_goals
FOR EACH ROW EXECUTE FUNCTION log_undo_action('user_goal');

DROP TRIGGER IF EXISTS trg_undo_credit_cards ON credit_cards;
CREATE TRIGGER trg_undo_credit_cards
AFTER INSERT OR UPDATE OR DELETE ON credit_cards
FOR EACH ROW EXECUTE FUNCTION log_undo_action('credit_card');
