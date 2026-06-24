-- Переименование тарифа 'start' -> 'base' с сохранением данных пользователей
UPDATE users SET subscription_tier = 'base' WHERE subscription_tier = 'start';

-- Добавляем новый тариф scan_text и обновляем лимиты для base/premium
INSERT INTO tier_limits (tier, ai_analyses_per_month, dashboards_per_month, receipt_scans_per_month, excel_imports_per_month, can_change_categories, can_change_currency, has_calendar, has_pnl, has_business_tools)
VALUES ('scan_text', 0, 0, -1, 0, TRUE, TRUE, FALSE, FALSE, FALSE)
ON CONFLICT (tier) DO UPDATE SET
    ai_analyses_per_month = EXCLUDED.ai_analyses_per_month,
    receipt_scans_per_month = EXCLUDED.receipt_scans_per_month,
    can_change_categories = EXCLUDED.can_change_categories,
    can_change_currency = EXCLUDED.can_change_currency;

-- Переносим лимиты тарифа 'start' (теперь не существует) в 'base'
INSERT INTO tier_limits (tier, ai_analyses_per_month, dashboards_per_month, receipt_scans_per_month, excel_imports_per_month, can_change_categories, can_change_currency, has_calendar, has_pnl, has_business_tools)
VALUES ('base', 0, 0, -1, 0, TRUE, TRUE, FALSE, FALSE, FALSE)
ON CONFLICT (tier) DO UPDATE SET
    ai_analyses_per_month = EXCLUDED.ai_analyses_per_month,
    receipt_scans_per_month = EXCLUDED.receipt_scans_per_month,
    can_change_categories = EXCLUDED.can_change_categories,
    can_change_currency = EXCLUDED.can_change_currency;

-- Удаляем старую строку 'start' из tier_limits (если осталась)
DELETE FROM tier_limits WHERE tier = 'start';
