ALTER TABLE transactions ADD COLUMN IF NOT EXISTS import_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_transactions_import_hash ON transactions(user_id, import_hash);

ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_voice_input BOOLEAN DEFAULT FALSE;
ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_annual_plan BOOLEAN DEFAULT FALSE;
ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_dds_categories BOOLEAN DEFAULT FALSE;
ALTER TABLE tier_limits ADD COLUMN IF NOT EXISTS has_export BOOLEAN DEFAULT FALSE;

UPDATE tier_limits
SET has_voice_input = tier IN ('base', 'premium', 'business'),
    has_export = tier IN ('premium', 'business'),
    has_dds_categories = tier IN ('premium', 'business'),
    has_annual_plan = tier IN ('premium', 'business');
