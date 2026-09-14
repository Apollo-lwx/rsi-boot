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
async def test_migrate_retry_does_not_duplicate_events(tmp_path, monkeypatch):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.memory.logstore import iter_events
    from rsi_boot.services import memory_migrate
    from rsi_boot.services.memory_migrate import MemoryMigrateHalfError, migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    kid = "d" * 32
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (kid, "local", "列名", "写列名", "convention", "active", now, now),
    )
    await db.execute(
        "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
        " latency_ms, status, feedback_token, retrieved_tags, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("log-retry", "req1", "u1", "local", "别用星号", 10, "success", "tok1",
         json.dumps([kid], ensure_ascii=False), now),
    )
    await db.commit()
    await db.close()

    def _locked(_rsi_dir):
        raise OSError("db locked")

    monkeypatch.setattr(memory_migrate, "_rename_legacy_db", _locked)
    with pytest.raises(MemoryMigrateHalfError):
        await migrate_workspace(tmp_path, lang="zh")
    first = [ev for ev in iter_events(rsi) if ev.get("id") == "log-retry"]
    assert len(first) == 1

    monkeypatch.undo()
    await migrate_workspace(tmp_path, lang="zh")
    again = [ev for ev in iter_events(rsi) if ev.get("id") == "log-retry"]
    assert len(again) == 1


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


async def _seed_and_migrate(tmp_path, content_type: str, status: str, kid: str):
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
        (kid, "local", "待审条目", "内容", content_type, status, now, now),
    )
    await db.commit()
    await db.close()
    await migrate_workspace(tmp_path, lang="zh")
    return rsi


@pytest.mark.asyncio
async def test_pending_review_prohibition_stays_under_pending(tmp_path):
    rsi = await _seed_and_migrate(tmp_path, "prohibition", "pending_review", "d" * 32)
    pending = list((rsi / "memory" / "pending" / "prohibitions").glob("*.yaml"))
    official = list((rsi / "memory" / "prohibitions").glob("*.yaml"))
    assert pending
    assert official == []
    dumped = yaml.safe_load(pending[0].read_text(encoding="utf-8"))
    assert dumped["status"] == "pending_review"


@pytest.mark.asyncio
async def test_pending_review_faq_stays_under_pending_not_official_docs(tmp_path):
    rsi = await _seed_and_migrate(tmp_path, "faq", "pending_review", "e" * 32)
    pending = list((rsi / "memory" / "pending").rglob("*.yaml"))
    official_docs = list((rsi / "memory" / "documentation").glob("*.yaml"))
    assert pending
    assert official_docs == []
    dumped = yaml.safe_load(pending[0].read_text(encoding="utf-8"))
    assert dumped["status"] == "pending_review"
    assert dumped["type"] == "documentation"
    assert dumped["extra"]["legacy_type"] == "faq"
    rel = pending[0].relative_to(rsi).as_posix()
    assert rel.startswith("memory/pending/")
    assert "memory/documentation/" not in rel


@pytest.mark.asyncio
async def test_archived_knowledge_goes_to_archive(tmp_path):
    rsi = await _seed_and_migrate(tmp_path, "convention", "archived", "f" * 32)
    archived = list((rsi / "memory" / "archive").glob("*.yaml"))
    official = list((rsi / "memory" / "conventions").glob("*.yaml"))
    pending = list((rsi / "memory" / "pending").rglob("*.yaml")) if (rsi / "memory" / "pending").exists() else []
    assert archived
    assert official == []
    assert pending == []
    dumped = yaml.safe_load(archived[0].read_text(encoding="utf-8"))
    assert dumped["status"] == "archived"
