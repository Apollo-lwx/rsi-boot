-- RSI Boot 003_rule_artifacts：v3.0 规则文件注入产物台账（Spec v3.0 §5.2）
-- 记录每次注入重写产出的文件与内容哈希：回滚重写、审计「宿主上下文里当前有哪些记忆」、
-- 启动时校验 rsi-* 文件与台账一致性（外部篡改检测）。

CREATE TABLE IF NOT EXISTS rule_artifacts (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    target         TEXT NOT NULL,              -- 载体标识：cursor_rule | agents_md | claude_skill
    target_path    TEXT NOT NULL,              -- 写出的文件路径（相对项目根）
    source_item_id TEXT REFERENCES knowledge_items(id),  -- 来源记忆（聚合文件为 NULL）
    content_hash   TEXT NOT NULL,              -- SHA-256，变更检测与审计
    status         TEXT NOT NULL DEFAULT 'active',  -- active | removed
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(project_id, target, target_path)
);

CREATE INDEX IF NOT EXISTS idx_rule_artifacts_project ON rule_artifacts(project_id, status);

-- 召回臂参数（Spec v3.0 §4.7）：intent='recall' 行的臂参数（JSON：top_n/threshold 等），
-- 复用 strategy_configs 的 Thompson 字段（alpha/beta/exposure_count/is_active）
ALTER TABLE strategy_configs ADD COLUMN params TEXT;
