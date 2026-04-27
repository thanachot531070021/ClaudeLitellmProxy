-- ===================================================
-- Claude Code Monitoring — Database Schema
-- ===================================================

-- ตาราง token usage หลัก
CREATE TABLE IF NOT EXISTS claude_usage (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ข้อมูล user
    user_email      TEXT,
    user_name       TEXT,
    department      TEXT,
    machine_id      TEXT,

    -- ข้อมูล session
    session_id      TEXT,
    model_name      TEXT,

    -- token counts
    input_tokens    BIGINT DEFAULT 0,
    output_tokens   BIGINT DEFAULT 0,
    cache_read_tokens   BIGINT DEFAULT 0,
    cache_create_tokens BIGINT DEFAULT 0,
    total_tokens    BIGINT GENERATED ALWAYS AS
                    (input_tokens + output_tokens + cache_read_tokens + cache_create_tokens) STORED,

    -- cost (USD)
    cost_usd        NUMERIC(10, 6) DEFAULT 0,

    -- metadata
    raw_attributes  JSONB
);

-- Index สำหรับ query เร็ว
CREATE INDEX idx_claude_usage_recorded_at  ON claude_usage (recorded_at DESC);
CREATE INDEX idx_claude_usage_user_email   ON claude_usage (user_email);
CREATE INDEX idx_claude_usage_session_id   ON claude_usage (session_id);
CREATE INDEX idx_claude_usage_department   ON claude_usage (department);

-- ===================================================
-- View: สรุป usage รายคน รายเดือน
-- ===================================================
CREATE OR REPLACE VIEW monthly_usage_by_user AS
SELECT
    DATE_TRUNC('month', recorded_at)    AS month,
    user_email,
    user_name,
    department,
    COUNT(DISTINCT session_id)          AS total_sessions,
    SUM(input_tokens)                   AS total_input_tokens,
    SUM(output_tokens)                  AS total_output_tokens,
    SUM(total_tokens)                   AS total_tokens,
    ROUND(SUM(cost_usd)::numeric, 4)   AS total_cost_usd,
    MODE() WITHIN GROUP (ORDER BY model_name) AS most_used_model
FROM claude_usage
GROUP BY 1, 2, 3, 4
ORDER BY 1 DESC, 7 DESC;

-- ===================================================
-- View: สรุป usage รายวัน (สำหรับ Grafana graph)
-- ===================================================
CREATE OR REPLACE VIEW daily_usage AS
SELECT
    DATE_TRUNC('day', recorded_at)     AS day,
    user_email,
    department,
    SUM(input_tokens)                   AS input_tokens,
    SUM(output_tokens)                  AS output_tokens,
    SUM(total_tokens)                   AS total_tokens,
    ROUND(SUM(cost_usd)::numeric, 4)   AS cost_usd,
    COUNT(DISTINCT session_id)          AS sessions
FROM claude_usage
GROUP BY 1, 2, 3
ORDER BY 1 DESC;

-- ===================================================
-- View: Top users this month
-- ===================================================
CREATE OR REPLACE VIEW top_users_this_month AS
SELECT
    user_email,
    user_name,
    department,
    SUM(total_tokens)                   AS total_tokens,
    ROUND(SUM(cost_usd)::numeric, 4)   AS total_cost_usd,
    COUNT(DISTINCT session_id)          AS sessions
FROM claude_usage
WHERE recorded_at >= DATE_TRUNC('month', NOW())
GROUP BY 1, 2, 3
ORDER BY total_tokens DESC
LIMIT 20;

-- ตรวจสอบ
SELECT 'Schema created successfully' AS status;
