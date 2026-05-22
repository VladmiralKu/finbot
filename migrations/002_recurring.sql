-- Регулярные платежи
CREATE TABLE IF NOT EXISTS recurring_payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    amount NUMERIC(12, 2),
    amount_is_approximate BOOLEAN DEFAULT FALSE,
    type VARCHAR(8) NOT NULL CHECK (type IN ('expense', 'income', 'goal')),
    kind VARCHAR(8) DEFAULT 'fixed',
    category_id INT REFERENCES categories(id) ON DELETE SET NULL,
    goal_id INT REFERENCES goals(id) ON DELETE SET NULL,
    
    -- Повторение
    repeat_type VARCHAR(16) NOT NULL CHECK (repeat_type IN ('monthly', 'weekly', 'daily')),
    repeat_day_of_month INT,        -- 1-31 для monthly
    repeat_day_of_week INT,         -- 0=пн, 6=вс для weekly
    
    -- Напоминания
    remind_days_before INT DEFAULT 1,  -- за сколько дней до платежа
    remind_time TIME DEFAULT '09:00',  -- время уведомления
    
    -- Статус
    is_active BOOLEAN DEFAULT TRUE,
    last_triggered_at TIMESTAMP,
    next_trigger_date DATE,
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- Лог отправленных уведомлений
CREATE TABLE IF NOT EXISTS notification_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    recurring_payment_id INT REFERENCES recurring_payments(id) ON DELETE CASCADE,
    sent_at TIMESTAMP DEFAULT NOW(),
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_recurring_user ON recurring_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_recurring_next ON recurring_payments(next_trigger_date, is_active);
