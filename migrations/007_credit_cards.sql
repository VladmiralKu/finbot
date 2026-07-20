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
);

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
);

CREATE TABLE IF NOT EXISTS credit_balance_requests (
    user_id BIGINT NOT NULL,
    request_month DATE NOT NULL,
    sent_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (user_id, request_month)
);

CREATE INDEX IF NOT EXISTS idx_credit_cards_user ON credit_cards(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_credit_card_events_card ON credit_card_events(user_id, card_id, event_date);
