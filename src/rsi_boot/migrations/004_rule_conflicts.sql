-- RSI Boot 004_rule_conflicts：v3.1 用户规则冲突检测（Spec v3.1 §4.9）
-- 学习记忆与用户手写规则（非 rsi- 前缀）的矛盾/过时/重复记录与裁决流转。

CREATE TABLE IF NOT EXISTS rule_conflicts (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    item_id           TEXT REFERENCES knowledge_items(id),  -- 冲突的学习记忆侧
    user_rule_path    TEXT NOT NULL,           -- 用户规则文件路径（相对项目根）
    user_rule_excerpt TEXT NOT NULL,           -- 冲突摘录（脱敏后，≤500 字符）
    user_rule_hash    TEXT NOT NULL,           -- 用户文件 SHA-256；memory_wins 后据此检测用户已修改
    conflict_type     TEXT NOT NULL,           -- contradiction | stale | overlap
    status            TEXT NOT NULL DEFAULT 'open',  -- open | user_wins | memory_wins | coexist
    resolution_note   TEXT,
    detected_at       TEXT NOT NULL,
    resolved_at       TEXT,
    UNIQUE(project_id, item_id, user_rule_path, conflict_type)  -- 防同一对组合重复提醒
);

CREATE INDEX IF NOT EXISTS idx_rule_conflicts_project ON rule_conflicts(project_id, status);

-- rejected+comment 禁止项提取（Spec v3.0 §4.4）：反馈评语脱敏后落库，
-- 供规则式提取生成 prohibition 草稿与失败挖掘的证据汇总
ALTER TABLE interaction_logs ADD COLUMN feedback_comment TEXT;
