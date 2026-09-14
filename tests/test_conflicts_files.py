"""File-backend conflict scan: YAML memories + event document ids."""

from pathlib import Path

import pytest
import yaml

from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore
from rsi_boot.memory.types import MemoryDoc


@pytest.mark.asyncio
async def test_conflict_scan_uses_document_ids(tmp_path):
    from rsi_boot.injector.conflict import ConflictDetector
    from rsi_boot.memory.logstore import append_event
    from rsi_boot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / ".rsi")
    append_event(tmp_path / ".rsi", {
        "id": "e1", "kind": "recall", "retrieved": ["a"*32], "task": "列名",
    })
    det = ConflictDetector(store=store, project_root=tmp_path)
    found = await det.scan()
    assert all("retrieved_tags" not in str(c) for c in found)


def test_conflict_source_has_no_like_tags():
    src = Path("src/rsi_boot/injector/conflict.py").read_text(encoding="utf-8")
    assert "retrieved_tags" not in src


@pytest.mark.asyncio
async def test_scan_writes_conflicts_yaml_with_document_id(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = "a" * 32
    store.write(
        MemoryDoc(id=doc_id, type="prohibition", title="禁止：使用裸 SQL", content="必须参数化"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-sql--aaaaaaaa.yaml",
    )
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "foo.mdc").write_text("数据库查询允许使用裸 SQL，方便快捷。", encoding="utf-8")

    det = ConflictDetector(store=store, project_root=tmp_path)
    stats = await det.scan()
    assert stats["detected"] == 1

    payload = yaml.safe_load((tmp_path / ".rsi" / "state" / "conflicts.yaml").read_text(encoding="utf-8"))
    items = payload["conflicts"] if isinstance(payload, dict) else payload
    assert items[0]["item_id"] == doc_id
    assert items[0]["status"] == "open"


@pytest.mark.asyncio
async def test_stale_adoption_uses_retrieved_ids(tmp_path):
    import os
    import time

    store = MemoryStore(tmp_path / ".rsi")
    doc_id = "b" * 32
    store.write(
        MemoryDoc(id=doc_id, type="prohibition", title="禁止：使用裸 SQL", content="必须参数化"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-sql--bbbbbbbb.yaml",
    )
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    stale = rules / "old.mdc"
    stale.write_text("禁止使用裸 SQL（旧规）。", encoding="utf-8")
    old = time.time() - 120 * 86400
    os.utime(stale, (old, old))

    for i in range(3):
        append_event(tmp_path / ".rsi", {
            "id": f"e{i}", "kind": "recall", "retrieved": [doc_id], "task": "SQL",
        })

    det = ConflictDetector(store=store, project_root=tmp_path)
    stats = await det.scan()
    assert stats["detected"] == 1
    payload = yaml.safe_load((tmp_path / ".rsi" / "state" / "conflicts.yaml").read_text(encoding="utf-8"))
    items = payload["conflicts"] if isinstance(payload, dict) else payload
    assert items[0]["type"] == "stale"
    assert items[0]["item_id"] == doc_id


@pytest.mark.asyncio
async def test_sqlite_conflict_helpers_refuse_file_store(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    det = ConflictDetector(store=store, project_root=tmp_path)
    with pytest.raises(TypeError, match="file store"):
        await det._close_resolved("p1", [])
    with pytest.raises(TypeError, match="file store"):
        await det._resolve_version({"item_id": "a" * 32, "project_id": "p1"}, "coexist", "now")
    with pytest.raises(TypeError, match="file store"):
        await det._resolve_knowledge_pair(
            {"item_id": "a" * 32, "project_id": "p1", "user_rule_path": "x", "user_rule_excerpt": ""},
            "coexist",
            "now",
        )
