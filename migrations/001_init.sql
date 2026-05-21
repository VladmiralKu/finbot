CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username VARCHAR(64),
    full_name VARCHAR(128),
    language_code VARCHAR(8) DEFAULT 'ru',
    is_premium BOOLEAN DEFAULT FALSE,
    premium_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(64) NOT NULL,
    type VARCHAR(8) NOT NULL CHECK (type IN ('expense', 'income')),
    kind VARCHAR(8) NOT NULL CHECK (kind IN ('fixed', 'variable', 'income')),
    is_default BOOLEAN DEFAULT FALSE,
    sort_order INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    category_id INT REFERENCES categories(id) ON DELETE SET NULL,
    amount NUMERIC(12, 2) NOT NULL,
    type VARCHAR(8) NOT NULL CHECK (type IN ('expense', 'income')),
    kind VARCHAR(8) NOT NULL CHECK (kind IN ('fixed', 'variable', 'income')),
    comment VARCHAR(256),
    receipt_photo_id VARCHAR(256),
    created_at TIMESTAMP DEFAULT NOW(),
    transaction_date DATE DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    target_amount NUMERIC(12, 2) NOT NULL,
    current_amount NUMERIC(12, 2) DEFAULT 0,
    deadline DATE,
    is_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    provider_payment_id VARCHAR(256),
    amount NUMERIC(8, 2),
    currency VARCHAR(8) DEFAULT 'USD',
    status VARCHAR(16) DEFAULT 'pending',
    premium_days INT DEFAULT 30,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_tx_user_month ON transactions(user_id, DATE_TRUNC('month', transaction_date));
CREATE INDEX IF NOT EXISTS idx_categories_user ON categories(user_id);

CREATE OR REPLACE FUNCTION create_default_categories(p_user_id BIGINT)
RETURNS VOID AS $$
BEGIN
    INSERT INTO categories (user_id, name, type, kind, is_default, sort_order) VALUES
    (p_user_id, 'Аренда / Ипотека',  'expense', 'fixed',    TRUE, 1),
    (p_user_id, 'Коммуналка',         'expense', 'fixed',    TRUE, 2),
    (p_user_id, 'Кредиты',            'expense', 'fixed',    TRUE, 3),
    (p_user_id, 'Подписки',           'expense', 'fixed',    TRUE, 4),
    (p_user_id, 'Еда / Продукты',     'expense', 'variable', TRUE, 5),
    (p_user_id, 'Транспорт',          'expense', 'variable', TRUE, 6),
    (p_user_id, 'Здоровье',           'expense', 'variable', TRUE, 7),
    (p_user_id, 'Одежда',             'expense', 'variable', TRUE, 8),
    (p_user_id, 'Развлечения',        'expense', 'variable', TRUE, 9),
    (p_user_id, 'Прочие расходы',     'expense', 'variable', TRUE, 10),
    (p_user_id, 'Зарплата',           'income',  'income',   TRUE, 1),
    (p_user_id, 'Фриланс',            'income',  'income',   TRUE, 2),
    (p_user_id, 'Прочие доходы',      'income',  'income',   TRUE, 3);
END;
$$ LANGUAGE plpgsql;
