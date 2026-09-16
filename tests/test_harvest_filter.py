"""Task 7：旧采集物识别 + 召回/search 可见集。"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.memory.harvest import HARVEST_TITLES, is_harvest_doc
from rsi_boot.memory.paths import official_dir, review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.recall_service import RecallService


def _doc(**kwargs) -> MemoryDoc:
    payload = dict(
        id=uuid.uuid4().hex,
        type="documentation",
        title="短知识",
        content="正文",
        status="active",
        tags=[],
    )
    payload.update(kwargs)
    return MemoryDoc(**payload)


def test_four_harvest_titles_are_harvest():
    for title in HARVEST_TITLES:
        assert is_harvest_doc(_doc(title=title))


def test_signal_code_active_is_harvest():
    assert is_harvest_doc(_doc(title="模块说明", tags=["signal:code"]))


def test_distilled_plus_docs_is_not_harvest():
    assert not is_harvest_doc(_doc(
        title="认证失败重试三次",
        tags=["signal:distilled", "signal:docs"],
    ))


def test_plain_convention_is_not_harvest():
    assert not is_harvest_doc(_doc(type="convention", title="API 用 pydantic"))


def _write(store: MemoryStore, doc: MemoryDoc, *, pending: bool = False) -> MemoryDoc:
    folder = review_dir(store.rsi_dir, doc.type) if pending else official_dir(store.rsi_dir, doc.type)
    dest = folder / memory_filename(doc.title, doc.id)
    return store.write(doc, dest=dest)


async def test_recall_excludes_harvest_keeps_distilled_active(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    harvest_id = uuid.uuid4().hex
    distilled_id = uuid.uuid4().hex
    pending_id = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要", content="class User 骨架足够长用于检索",
        tags=["signal:code"],
    ))
    _write(store, _doc(
        id=distilled_id, title="认证失败重试三次",
        content="登录失败后最多重试三次再锁定",
        tags=["signal:distilled", "signal:docs"],
    ))
    _write(store, _doc(
        id=pending_id, title="待审蒸馏条",
        content="pending distilled 不应进召回",
        tags=["signal:distilled"],
        status="pending_review",
    ), pending=True)

    result = await RecallService(store=store, feedback_secret="s").recall(
        "认证失败重试三次怎么做", "p1",
    )
    recalled_ids = {item["id"] for item in result["items"]}
    recalled_ids.update(p["id"] for p in result["prohibitions"])
    assert harvest_id not in recalled_ids
    assert distilled_id in recalled_ids
    assert pending_id not in recalled_ids


async def test_search_sees_pending_short_knowledge_not_harvest(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    harvest_id = uuid.uuid4().hex
    pending_distill = uuid.uuid4().hex
    pending_note = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要", content="class User 骨架足够长用于检索",
        tags=["signal:code"],
    ))
    _write(store, _doc(
        id=pending_distill, title="认证失败重试三次",
        content="登录失败后最多重试三次再锁定",
        tags=["signal:distilled"],
        status="pending_review",
    ), pending=True)
    _write(store, _doc(
        id=pending_note, type="convention", title="记下来用 UTC",
        content="日志时间一律用 UTC，不要用本地时区",
        status="pending_review",
    ), pending=True)

    svc = KnowledgeService(store=store, project_root=tmp_path)
    hits = await svc.search("认证失败重试三次 UTC 日志", "p")
    ids = {it.id.hex for it in hits}
    assert harvest_id not in ids
    assert pending_distill in ids
    assert pending_note in ids


async def test_conflict_scan_skips_harvest_docs(tmp_path):
    root = tmp_path / "proj"
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "team.mdc").write_text("允许使用 SELECT * 查询用户表。\n", encoding="utf-8")
    store = MemoryStore(root / ".rsi")
    harvest_id = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, type="prohibition", title="禁止 SELECT *",
        content="查询必须显式列字段",
        tags=["signal:code"],
    ))
    detector = ConflictDetector(store=store, project_root=root)
    await detector.scan("p1")
    rows = await detector.list_conflicts("p1", "open")
    assert not any(r.get("item_id") == harvest_id for r in rows)


def test_list_official_skip_harvest_does_not_parse_body(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / ".rsi")
    harvest_id = uuid.uuid4().hex
    keep_id = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要",
        content=("旧采集正文 " * 400),
        tags=["signal:docs"],
    ))
    _write(store, _doc(
        id=keep_id, title="认证失败重试三次",
        content="登录失败后最多重试三次再锁定",
        tags=["signal:distilled"],
    ))
    parsed: list[str] = []
    real = MemoryStore._safe_load

    def spy(self, path):
        parsed.append(Path(path).name)
        return real(self, path)

    monkeypatch.setattr(MemoryStore, "_safe_load", spy)
    docs = store.list_official("documentation", skip_harvest=True)
    ids = {d.id for d in docs}
    assert harvest_id not in ids
    assert keep_id in ids
    assert not any(harvest_id[:8] in name for name in parsed)


def test_first_read_does_not_hydrate_unrelated_harvest(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / ".rsi")
    harvest_id = uuid.uuid4().hex
    keep_id = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要",
        content=("旧采集正文 " * 400),
        tags=["signal:docs"],
    ))
    _write(store, _doc(
        id=keep_id, type="convention", title="日志用 UTC",
        content="日志时间一律用 UTC",
    ))
    parsed: list[str] = []
    real = MemoryStore._safe_load

    def spy(self, path):
        parsed.append(Path(path).name)
        return real(self, path)

    monkeypatch.setattr(MemoryStore, "_safe_load", spy)
    fresh = MemoryStore(tmp_path / ".rsi")
    got = fresh.read(keep_id)
    assert got.id == keep_id
    assert not any(harvest_id[:8] in name for name in parsed)


def test_prune_catalog_does_not_parse_harvest_bodies(tmp_path, monkeypatch):
    from rsi_boot.memory.graph import add_edge, load_catalog, prune_catalog

    store = MemoryStore(tmp_path / ".rsi")
    harvest_id = uuid.uuid4().hex
    keep_id = uuid.uuid4().hex
    missing = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要",
        content=("旧采集正文 " * 400),
        tags=["signal:docs"],
    ))
    _write(store, _doc(
        id=keep_id, title="认证失败重试三次",
        content="登录失败后最多重试三次再锁定",
        tags=["signal:distilled"],
    ))
    add_edge(store.rsi_dir, harvest_id, keep_id, "cites")
    add_edge(store.rsi_dir, keep_id, missing, "cites")
    parsed: list[str] = []
    real = MemoryStore._safe_load

    def spy(self, path):
        parsed.append(Path(path).name)
        return real(self, path)

    monkeypatch.setattr(MemoryStore, "_safe_load", spy)
    prune_catalog(store.rsi_dir, store)
    edges = load_catalog(store.rsi_dir)["edges"]
    assert any(e.get("from") == harvest_id and e.get("to") == keep_id for e in edges)
    assert not any(e.get("to") == missing for e in edges)
    assert not any(harvest_id[:8] in name for name in parsed)


async def test_bootstrap_sets_harvest_warning_without_deleting(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n\n" + "正文足够长。" * 20, encoding="utf-8")
    store = MemoryStore(root / ".rsi")
    harvest_id = uuid.uuid4().hex
    _write(store, _doc(
        id=harvest_id, title="代码骨架摘要", content="旧采集物应保留",
        tags=["signal:code"],
    ))
    args = argparse.Namespace(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=True, local_judge=False,
    )
    assert await run_bootstrap(args) == 0
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert "1" in report["harvest_warning"]
    assert store.read(harvest_id).status == "active"
