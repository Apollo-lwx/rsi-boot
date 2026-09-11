async def test_migration_applies_and_is_idempotent(db):
    conn = await db.connect()
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    assert row[0] >= 1

    # 幂等：再次执行不报错、版本不变
    from rsi_boot.data.migrate import migrate
    assert await migrate(db) == row[0]

    # 核心表存在
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('interaction_logs', 'knowledge_items', 'strategy_configs', 'knowledge_fts')"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert names == {"interaction_logs", "knowledge_items", "strategy_configs", "knowledge_fts"}


async def test_failed_migration_rolls_back(db, monkeypatch):
    """坏迁移：SQL 报错 → 回滚 → user_version 不变 → 修复后可重入（中断重入）"""
    import pytest

    from rsi_boot.data import migrate as migrate_mod

    conn = await db.connect()
    async with conn.execute("PRAGMA user_version") as cur:
        version_before = (await cur.fetchone())[0]

    bad = [(version_before + 1, "CREATE TABLE should_not_exist (id INTEGER); BROKEN SQL HERE;")]
    monkeypatch.setattr(migrate_mod, "_load_migrations", lambda: bad)
    with pytest.raises(Exception):
        await migrate_mod.migrate(db)

    async with conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == version_before
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'should_not_exist'"
    ) as cur:
        assert await cur.fetchone() is None

    # 修复后重入：正常迁移可继续推进版本
    good = [(version_before + 1, "CREATE TABLE reentry_ok (id INTEGER);")]
    monkeypatch.setattr(migrate_mod, "_load_migrations", lambda: good)
    assert await migrate_mod.migrate(db) == version_before + 1
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'reentry_ok'"
    ) as cur:
        assert await cur.fetchone() is not None


async def test_fts_triggers_sync(db):
    """001 迁移的 FTS 同步触发器：insert/delete 自动同步 knowledge_fts"""
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, roles, tags, status, created_at, updated_at) "
        "VALUES ('k1', 'p1', 'hello world', 'docker compose deploy', 'documentation', '[]', '[]', 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    await conn.commit()
    async with conn.execute("SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH '\"docker\"'") as cur:
        assert await cur.fetchone() is not None

    await conn.execute("DELETE FROM knowledge_items WHERE id = 'k1'")
    await conn.commit()
    async with conn.execute("SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH '\"docker\"'") as cur:
        assert await cur.fetchone() is None
