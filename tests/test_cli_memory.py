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


def test_cli_memory_need_sub(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    assert run_memory([]) == 2
    assert capsys.readouterr().err.strip() == t("MEMORY_NEED_SUB", "zh")


def test_cli_memory_lang_overrides_env(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "en")
    assert run_memory(["--lang", "zh"]) == 2
    assert capsys.readouterr().err.strip() == t("MEMORY_NEED_SUB", "zh")


def _seed_memory_docs(tmp_path):
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc

    store = MemoryStore(tmp_path / ".rsi")
    titles = (
        "禁止 SELECT 星号",
        "必须列出列名",
        "不要用 SELECT 星号",
        "列名优于星号",
        "查询必须写列",
        "SQL 星号禁止",
    )
    docs = []
    for i, title in enumerate(titles):
        doc_id = f"{i + 1:08x}{'a' * 24}"
        dest = official_dir(store.rsi_dir, "prohibition") / f"p--{doc_id[:8]}.yaml"
        docs.append(
            store.write(
                MemoryDoc(id=doc_id, type="prohibition", title=title, content=f"{title} 必须列字段"),
                dest=dest,
            )
        )
    return docs


def test_cli_memory_index_table_and_json(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    docs = _seed_memory_docs(tmp_path)

    assert run_memory(["index"]) == 0
    table = capsys.readouterr().out.strip().splitlines()
    assert table[0].split() == ["id", "type", "status", "title", "path"]
    assert any(docs[0].id in line for line in table[1:])

    assert run_memory(["index", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    row = next(item for item in payload if item["id"] == docs[0].id)
    assert row["type"] == "prohibition"
    assert row["status"] == "active"
    assert row["title"] == docs[0].title
    assert row["path"]
    assert set(row) >= {"id", "type", "status", "title", "path"}


def test_cli_memory_open_yaml_and_missing(tmp_path, capsys, monkeypatch):
    import yaml

    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    docs = _seed_memory_docs(tmp_path)

    assert run_memory(["open", docs[0].id]) == 0
    body = yaml.safe_load(capsys.readouterr().out)
    assert body["id"] == docs[0].id
    assert "必须列字段" in body["content"]

    missing = "f" * 32
    assert run_memory(["open", missing]) == 3
    assert capsys.readouterr().err.strip() == t("NOT_FOUND", "zh", id=missing)


def test_cli_memory_reindex_cache_stable(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.rag.index import search
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    _seed_memory_docs(tmp_path)
    cache = tmp_path / ".rsi" / "cache" / "inverted.json"

    assert run_memory(["reindex"]) == 0
    assert capsys.readouterr().out.strip() == t("REINDEX_DONE", "zh")
    assert cache.is_file()
    first = json.loads(cache.read_text(encoding="utf-8"))

    cache.unlink()
    assert not cache.exists()
    assert run_memory(["reindex"]) == 0
    capsys.readouterr()
    assert cache.is_file()
    second = json.loads(cache.read_text(encoding="utf-8"))

    query = "列名 星号 SELECT"
    top1 = [doc_id for doc_id, _ in search(first, query, top_n=5)]
    top2 = [doc_id for doc_id, _ in search(second, query, top_n=5)]
    union = set(top1) | set(top2)
    overlap = (len(set(top1) & set(top2)) / len(union)) if union else 1.0
    assert overlap >= 0.8


def test_cli_memory_graph_prints_mermaid(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore, memory_filename
    from rsi_boot.memory.types import MemoryDoc

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    store = MemoryStore(tmp_path / ".rsi")
    a_id, b_id = "a" * 32, "b" * 32
    store.write(
        MemoryDoc(id=a_id, type="convention", title="约定甲", content="写法甲"),
        dest=official_dir(store.rsi_dir, "convention") / memory_filename("约定甲", a_id),
    )
    store.write(
        MemoryDoc(id=b_id, type="convention", title="约定乙", content="写法乙"),
        dest=official_dir(store.rsi_dir, "convention") / memory_filename("约定乙", b_id),
    )
    catalog = tmp_path / ".rsi" / "state" / "catalog.yaml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(
        "edges:\n  - {from: '" + a_id + "', to: '" + b_id + "', rel: cites}\n",
        encoding="utf-8",
    )
    assert run_memory(["graph"]) == 0
    out = capsys.readouterr().out
    assert a_id in out
    assert b_id in out
    assert "graph" in out.lower() or "-->" in out
    assert list((tmp_path / ".rsi" / "memory").rglob("*.md")) == []


def test_cli_memory_help_avoids_db_words(monkeypatch):
    from rsi_boot.cli.memory_command import _parser
    from rsi_boot.__main__ import build_parser

    monkeypatch.setenv("RSI_LANG", "zh")
    mem_help = _parser().format_help()
    assert "数据库" not in mem_help
    assert "rsi.db" not in mem_help

    root_help = build_parser().format_help()
    # only the memory / learn choice lines — wipe/migrate still mention rsi.db
    mem_line = next(line for line in root_help.splitlines() if line.strip().startswith("memory"))
    learn_line = next(line for line in root_help.splitlines() if line.strip().startswith("learn"))
    assert "数据库" not in mem_line and "rsi.db" not in mem_line
    assert "数据库" not in learn_line and "rsi.db" not in learn_line
    assert "迁出/索引/打开" in mem_line
    assert "教学与收工" in learn_line
