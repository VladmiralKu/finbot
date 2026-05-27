-- Новые поля в transactions
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS wallet VARCHAR(16) DEFAULT 'cash'
    CHECK (wallet IN ('cash', 'card', 'other'));
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS pnl_period VARCHAR(7);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS currency VARCHAR(8) DEFAULT 'RUB';

-- Настройки пользователя
CREATE TABLE IF NOT EXISTS user_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    currency VARCHAR(8) DEFAULT 'RUB',
    timezone VARCHAR(32) DEFAULT 'Europe/Moscow',
    language VARCHAR(8) DEFAULT 'ru',
    wallet_names JSONB DEFAULT '{"cash": "Наличные", "card": "Безнал", "other": "Другое"}',
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Тарифы и цены
CREATE TABLE IF NOT EXISTS subscription_plans (
    id SERIAL PRIMARY KEY,
    code VARCHAR(16) UNIQUE NOT NULL,
    name VARCHAR(64) NOT NULL,
    tier VARCHAR(16) NOT NULL,
    period_months INT NOT NULL DEFAULT 1,
    price_rub NUMERIC(10,2) NOT NULL,
    price_stars INT,
    discount_pct INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);

INSERT INTO subscription_plans (code, name, tier, period_months, price_rub, price_stars, discount_pct) VALUES
('start_1m',    'Старт — 1 месяц',      'start',    1,  79,    0,    0),
('start_3m',    'Старт — 3 месяца',     'start',    3,  225,   0,    5),
('start_6m',    'Старт — 6 месяцев',    'start',    6,  427,   0,   10),
('premium_1m',  'Premium — 1 месяц',    'premium',  1,  350,   150,  0),
('premium_3m',  'Premium — 3 месяца',   'premium',  3,  998,   428,  5),
('premium_6m',  'Premium — 6 месяцев',  'premium',  6,  1890,  810, 10),
('business_1m', 'Business — 1 месяц',   'business', 1,  1490,  300,  0),
('business_3m', 'Business — 3 месяца',  'business', 3,  4247,  855,  5),
('business_6m', 'Business — 6 месяцев', 'business', 6,  8046,  1620,10);

-- Лимиты ИИ по тарифам
CREATE TABLE IF NOT EXISTS tier_limits (
    tier VARCHAR(16) PRIMARY KEY,
    ai_analyses_per_month INT DEFAULT 0,
    dashboards_per_month INT DEFAULT 0,
    receipt_scans_per_month INT DEFAULT 0,
    excel_imports_per_month INT DEFAULT 0,
    can_change_categories BOOLEAN DEFAULT FALSE,
    can_change_currency BOOLEAN DEFAULT FALSE,
    has_calendar BOOLEAN DEFAULT FALSE,
    has_pnl BOOLEAN DEFAULT FALSE,
    has_business_tools BOOLEAN DEFAULT FALSE
);

INSERT INTO tier_limits VALUES
('free',     0,   0,  0,  0, FALSE, FALSE, FALSE, FALSE, FALSE),
('start',    0,   0,  -1, 0, TRUE,  TRUE,  FALSE, FALSE, FALSE),
('premium',  10,  10, -1, 5, TRUE,  TRUE,  TRUE,  TRUE,  FALSE),
('business', -1,  -1, -1, -1,TRUE,  TRUE,  TRUE,  TRUE,  TRUE);

-- Лог использования ИИ
CREATE TABLE IF NOT EXISTS ai_usage (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    usage_type VARCHAR(32) NOT NULL,
    used_at TIMESTAMP DEFAULT NOW(),
    month_year VARCHAR(7) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_user_month ON ai_usage(user_id, month_year);
CREATE INDEX IF NOT EXISTS idx_transactions_wallet ON transactions(user_id, wallet);
CREATE INDEX IF NOT EXISTS idx_transactions_pnl ON transactions(user_id, pnl_period);
