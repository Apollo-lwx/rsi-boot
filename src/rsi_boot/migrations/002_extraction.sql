-- RSI Boot 002_extraction：P3.4 知识提取（§4.3）
-- response_excerpt：脱敏后的响应摘录（≤4000 字符），供知识提取候选与差评复查使用；
-- 响应正文不全文落库，摘录仅服务于本地学习链路，随日志一并按 90 天保留期归档删除（§8.3）。

ALTER TABLE interaction_logs ADD COLUMN response_excerpt TEXT;

-- 检索命中条目的 domain/tags 快照（JSON 数组）：copied/referenced 反馈时按 §4.2
-- 对 knowledge_interests 命中标签 +1；亦为离线聚合提供逐条命中依据（§3.8）
ALTER TABLE interaction_logs ADD COLUMN retrieved_tags TEXT;

-- 知识提取候选队列：modified diff>20%（反馈时写入）与 rating ≥ 4（每日任务汇集）
CREATE TABLE IF NOT EXISTS extraction_candidates (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    source_log_id  TEXT NOT NULL,
    candidate_type TEXT NOT NULL,           -- modified | high_rated
    question       TEXT NOT NULL,
    answer         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending | extracted | dismissed
    created_at     TEXT NOT NULL,
    processed_at   TEXT,
    UNIQUE(source_log_id, candidate_type)
);

CREATE INDEX IF NOT EXISTS idx_candidates_status ON extraction_candidates(status, created_at);
