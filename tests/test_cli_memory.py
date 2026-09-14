"""CLI: rsi memory migrate — no-db, --json, mid-fail HALF."""

from __future__ import annotations

import asyncio
import json

from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t


async def _seed_prohibition(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient

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
    return rsi


def test_cli_migrate_no_db_exit_3(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    code = run_memory(["migrate"])
    assert code == 3
    assert capsys.readouterr().err.strip() == t("MIGRATE_NO_DB", "zh")


def test_cli_migrate_json_stdout_is_object(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    asyncio.run(_seed_prohibition(tmp_path))

    code = run_memory(["migrate", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)
    assert "knowledge" in payload
    assert "[1/" not in captured.out
    assert t("MIGRATE_PHASE_OPEN", "zh") not in captured.out


def test_cli_mid_fail_half_leaves_db(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    rsi = asyncio.run(_seed_prohibition(tmp_path))

    def _boom(self, doc, *, dest):
        raise OSError("disk full")

    monkeypatch.setattr("rsi_boot.memory.store.MemoryStore.write", _boom)
    lang = locale_lang()
    code = run_memory(["migrate"])
    assert code == 3
    assert capsys.readouterr().err.strip() == t("MIGRATE_HALF", lang)
    assert (rsi / "rsi.db").exists()
    errors = rsi / "logs" / "errors.jsonl"
    assert errors.is_file()
    assert errors.read_text(encoding="utf-8").strip()
