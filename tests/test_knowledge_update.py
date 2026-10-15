"""rsi_knowledge_update：原地改字段（保留 id/目录）+ merge 合并近重复（归档较弱者+supersedes 边）。"""

from datetime import datetime, timezone

import pytest

from rsi_boot.memory.graph import load_catalog
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.knowledge_service import KnowledgeService


def _write_convention(store, *, idx, title, content, **kw):
    doc_id = f"{idx:08x}{'b' * 24}"
    doc = MemoryDoc(id=doc_id, type="convention", title=title, content=content,
                    status="active", **kw)
    dest = official_dir(store.rsi_dir, "convention") / memory_filename(title, doc_id)
    store.write(doc, dest=dest)
    return doc_id


def _svc(store):
    return KnowledgeService(store)


# ---------- update ----------

@pytest.mark.asyncio
async def test_update_changes_fields_preserves_id_and_dir(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write_convention(store, idx=1, title="旧题", content="旧内容", domain="backend")
    out = await _svc(store).update(doc_id, {
        "title": "新题", "content": "新内容", "tags": ["x", "y"], "domain": "frontend",
    })
    assert out["status"] == "success"
    assert out["updated"] == ["content", "domain", "tags", "title"]
    reloaded = store.read(doc_id)
    assert reloaded.id == doc_id                      # id 不变
    assert reloaded.title == "新题"
    assert reloaded.content == "新内容"
    assert reloaded.tags == ["x", "y"]
    assert reloaded.domain == "frontend"
    assert reloaded.status == "active"
    assert out["data"]["path"]                        # 仍在原目录


@pytest.mark.asyncio
async def test_update_empty_patch_is_invalid(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write_convention(store, idx=2, title="甲", content="乙")
    out = await _svc(store).update(doc_id, {"status": "archived"})   # 非可改字段
    assert out["status"] == "error" and out["code"] == "invalid"


@pytest.mark.asyncio
async def test_update_rejects_overlong_short_knowledge(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write_convention(store, idx=3, title="甲", content="乙")
    out = await _svc(store).update(doc_id, {"content": "x" * 1600})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_update_case_type_allows_longer_content(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = f"{'4' * 8}{'c' * 24}"
    doc = MemoryDoc(id=doc_id, type="teaching_case", title="案例", content="初")
    store.write(doc, dest=official_dir(store.rsi_dir, "teaching_case") / memory_filename("案例", doc_id))
    out = await _svc(store).update(doc_id, {"content": "长" * 2000})   # 案例类 >1500 允许
    assert out["status"] == "success"


@pytest.mark.asyncio
async def test_update_unknown_id_not_found(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    out = await _svc(store).update("deadbeef" * 4, {"title": "x"})
    assert out["status"] == "error" and out["code"] == "not_found"


# ---------- merge ----------

@pytest.mark.asyncio
async def test_merge_keeps_more_complete_and_archives_weaker(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    keep = _write_convention(store, idx=10, title="完整", content="很详细" * 20)
    weak = _write_convention(store, idx=11, title="简略", content="短")
    out = await _svc(store).merge([weak, keep])
    assert out["status"] == "success"
    assert out["kept"] == keep                        # 正文最长者留
    assert out["archived"] == [weak]
    assert out["edges"] == 1
    assert store.read(weak).status == "archived"      # 非硬删，移入 archive
    kept = store.read(keep)
    assert any(m["id"] == weak for m in kept.extra.get("merged_from") or [])
    edges = [e for e in load_catalog(store.rsi_dir)["edges"] if e.get("rel") == "supersedes"]
    assert any(e["from"] == keep and e["to"] == weak for e in edges)


@pytest.mark.asyncio
async def test_merge_respects_explicit_keep_id(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = _write_convention(store, idx=20, title="A", content="甲乙丙丁戊")
    b = _write_convention(store, idx=21, title="B", content="短")
    out = await _svc(store).merge([a, b], keep_id=b)
    assert out["kept"] == b and out["archived"] == [a]


@pytest.mark.asyncio
async def test_merge_needs_two_ids(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = _write_convention(store, idx=30, title="A", content="甲")
    assert (await _svc(store).merge([a]))["status"] == "error"
    assert (await _svc(store).merge([a, "ff" * 16]))["status"] == "error"  # 只有一个存在


# ---------- tool ----------

@pytest.mark.asyncio
async def test_knowledge_update_tool_update_and_merge(tmp_path):
    from rsi_boot.api.tools.knowledge_update_tool import handle

    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store)
    doc_id = _write_convention(store, idx=40, title="旧", content="旧文")
    out = await handle(svc, {"action": "update", "id": doc_id, "content": "新文"})
    assert out["status"] == "success"
    assert store.read(doc_id).content == "新文"

    keep = _write_convention(store, idx=41, title="长", content="详" * 30)
    weak = _write_convention(store, idx=42, title="短", content="略")
    merged = await handle(svc, {"action": "merge", "ids": [keep, weak]})
    assert merged["status"] == "success" and merged["kept"] == keep
