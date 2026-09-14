"""sqlite → YAML migrate_workspace: dest dirs, logs, dry_run, progress phases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from rsi_boot.ux.messages import t


@pytest.mark.asyncio
async def test_migrate_writes_yaml_and_renames_db(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("a" * 32, "local", "禁止 SELECT *", "必须列字段", "prohibition", "active", now, now),
    )
    await db.commit()
    await db.close()
    await migrate_workspace(tmp_path, lang="zh")
    assert not (rsi / "rsi.db").exists()
    assert list((rsi / "rsi.db.bak-*").parent.glob("rsi.db.bak-*")) or list(rsi.glob("rsi.db.bak-*"))
    assert list((rsi / "memory" / "prohibitions").glob("*.yaml"))


@pytest.mark.asyncio
async def test_dry_run_does_not_create_yaml_or_rename(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("a" * 32, "local", "禁止 SELECT *", "必须列字段", "prohibition", "active", now, now),
    )
    await db.commit()
    await db.close()
    counts = await migrate_workspace(tmp_path, dry_run=True, lang="zh")
    assert counts["knowledge"] == 1
    assert (rsi / "rsi.db").exists()
    assert not (rsi / "memory_migrate.yaml").exists()
    mem = rsi / "memory"
    yaml_files = list(mem.rglob("*.yaml")) if mem.exists() else []
    assert yaml_files == []
    assert list(rsi.glob("rsi.db.bak-*")) == []


@pytest.mark.asyncio
async def test_mid_fail_leaves_rsi_db(tmp_path, monkeypatch):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import MemoryMigrateHalfError, migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("a" * 32, "local", "禁止 SELECT *", "必须列字段", "prohibition", "active", now, now),
    )
    await db.commit()
    await db.close()

    def _boom(self, doc, *, dest):
        raise OSError("disk full")

    monkeypatch.setattr("rsi_boot.memory.store.MemoryStore.write", _boom)
    with pytest.raises(MemoryMigrateHalfError):
        await migrate_workspace(tmp_path, lang="zh")
    assert (rsi / "rsi.db").exists()
    assert list(rsi.glob("rsi.db.bak-*")) == []
    errors = rsi / "logs" / "errors.jsonl"
    assert errors.is_file()


@pytest.mark.asyncio
async def test_log_non_id_retrieved_tags_go_to_legacy(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.memory.logstore import iter_events
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    kid = "b" * 32
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (kid, "local", "列名", "写列名", "convention", "active", now, now),
    )
    await db.execute(
        "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
        " latency_ms, status, feedback_token, retrieved_tags, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "log1",
            "req1",
            "u1",
            "local",
            "别用星号",
            10,
            "success",
            "tok1",
            json.dumps([kid, "caching", "backend"], ensure_ascii=False),
            now,
        ),
    )
    await db.commit()
    await db.close()
    await migrate_workspace(tmp_path, lang="zh")
    events = list(iter_events(rsi))
    assert events
    ev = events[0]
    assert ev["retrieved"] == [kid]
    assert ev["retrieved_legacy_tags"] == ["caching", "backend"]
    for item in ev["retrieved"]:
        assert isinstance(item, str) and len(item) == 32


@pytest.mark.asyncio
async def test_experience_maps_to_convention_with_legacy_type(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("c" * 32, "local", "经验条目", "这样更好", "experience", "active", now, now),
    )
    await db.commit()
    await db.close()
    await migrate_workspace(tmp_path, lang="zh")
    yamls = list((rsi / "memory" / "conventions").glob("*.yaml"))
    assert yamls
    dumped = yaml.safe_load(yamls[0].read_text(encoding="utf-8"))
    assert dumped["type"] == "convention"
    assert dumped["extra"]["legacy_type"] == "experience"
    assert dumped["id"] == "c" * 32


@pytest.mark.asyncio
async def test_progress_eight_phases_use_exact_t_strings(tmp_path):
    from rsi_boot.cli.progress import Progress
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    await db.close()

    lang = "zh"
    names: list[str] = []
    orig = Progress.phase

    def _capture(self, name, total=None):
        names.append(name)
        return orig(self, name, total)

    progress = Progress(stream=open(tmp_path / "prog.txt", "w", encoding="utf-8"), min_interval=0)
    Progress.phase = _capture  # type: ignore[method-assign]
    try:
        await migrate_workspace(tmp_path, progress=progress, lang=lang)
    finally:
        Progress.phase = orig  # type: ignore[method-assign]
        progress.stream.close()

    expected = [
        t("MIGRATE_PHASE_OPEN", lang),
        t("MIGRATE_PHASE_KNOWLEDGE", lang),
        t("MIGRATE_PHASE_LOGS", lang),
        t("MIGRATE_PHASE_STATE", lang),
        t("MIGRATE_PHASE_WRITE", lang),
        t("MIGRATE_PHASE_BAK", lang),
        t("MIGRATE_PHASE_INDEX", lang),
        t("MIGRATE_PHASE_INJECT", lang),
    ]
    assert names == expected
