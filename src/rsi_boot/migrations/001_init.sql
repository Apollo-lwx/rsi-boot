-- RSI Boot 001_init：M1 全量表结构（Spec §2.2）
-- 注意：knowledge_vectors（vec0 虚拟表）不在迁移中创建——它依赖 sqlite-vec 扩展，
-- 由 data/vec.py 在 vector.backend=sqlite-vec 且扩展可用时于运行时创建（§2.3 回退设计）。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS interaction_logs (
    id                TEXT PRIMARY KEY,
    request_id        TEXT NOT NULL UNIQUE,
    user_id           TEXT NOT NULL,
    project_id        TEXT,
    session_id        TEXT,
    raw_input         TEXT NOT NULL,
    context           TEXT,
    intent            TEXT,
    intent_confidence REAL,
    strategy_name     TEXT,
    model_name        TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    cost_usd          REAL,
    latency_ms        INTEGER NOT NULL,
    quality_score     REAL,
    status            TEXT NOT NULL DEFAULT 'pending',
    feedback_token    TEXT NOT NULL UNIQUE,
    feedback_action   TEXT,
    feedback_rating   INTEGER CHECK (feedback_rating BETWEEN 1 AND 5),
    created_at        TEXT NOT NULL,
    completed_at      TEXT
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      TEXT NOT NULL,
    project_id   TEXT NOT NULL DEFAULT '',
    profile_data TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

CREATE TABLE IF NOT EXISTS project_configs (
    project_id  TEXT PRIMARY KEY,
    config_data TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    intent         TEXT NOT NULL,
    role           TEXT,
    strategy_name  TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    template_ref   TEXT,
    weight         REAL DEFAULT 1.0,
    alpha          REAL NOT NULL DEFAULT 1.0,
    beta           REAL NOT NULL DEFAULT 1.0,
    exposure_count INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_type TEXT DEFAULT 'documentation',
    roles        TEXT DEFAULT '[]',
    domain       TEXT,
    tags         TEXT DEFAULT '[]',
    source_url   TEXT,
    status       TEXT DEFAULT 'active',
    embedding    BLOB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_proposals (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    slot              TEXT NOT NULL,
    target_ref        TEXT NOT NULL,
    action            TEXT NOT NULL,
    payload           TEXT NOT NULL,
    evidence          TEXT,
    regression_report TEXT,
    status            TEXT NOT NULL DEFAULT 'proposed',
    created_at        TEXT NOT NULL,
    decided_at        TEXT,
    activated_at      TEXT,
    observe_until     TEXT
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    version           INTEGER NOT NULL,
    trigger_proposal  TEXT,
    slots             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL,
    UNIQUE(project_id, version)
);

-- 全文检索表（contentless 模式，内容仍存 knowledge_items，避免双份存储）
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, content, tags,
    content='knowledge_items', content_rowid='rowid'
);

-- knowledge_items -> knowledge_fts 增量同步触发器（contentless 模式需手动维护）
CREATE TRIGGER IF NOT EXISTS knowledge_items_ai AFTER INSERT ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_items_ad AFTER DELETE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_items_au AFTER UPDATE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO knowledge_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE INDEX IF NOT EXISTS idx_logs_created_at ON interaction_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_intent ON interaction_logs(intent);
CREATE INDEX IF NOT EXISTS idx_logs_status ON interaction_logs(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge_items(project_id, status);
CREATE INDEX IF NOT EXISTS idx_strategy_lookup ON strategy_configs(project_id, intent, role);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON harness_proposals(status, project_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON config_snapshots(project_id, version DESC);
