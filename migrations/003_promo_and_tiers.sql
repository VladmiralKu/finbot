-- Добавляем уровень подписки
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_tier VARCHAR(16) DEFAULT 'free';

-- Промокоды
CREATE TABLE IF NOT EXISTS promo_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(32) UNIQUE NOT NULL,
    tier VARCHAR(16) DEFAULT 'premium',
    days INT DEFAULT 30,
    max_uses INT DEFAULT 1,
    used_count INT DEFAULT 0,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Лог использования промокодов
CREATE TABLE IF NOT EXISTS promo_uses (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    promo_id INT REFERENCES promo_codes(id),
    used_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_code ON promo_codes(code);
