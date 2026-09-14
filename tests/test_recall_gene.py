import json
import shutil

import pytest

from rsi_boot.memory.logstore import append_event, iter_events
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.recall_service import RecallService
from rsi_boot.ux.messages import t


def _hex(ch: str) -> str:
    return ch * 32


def _write(store: MemoryStore, doc: MemoryDoc, *, sub: str = "") -> MemoryDoc:
    root = official_dir(store.rsi_dir, doc.type)
    dest = (root / sub / memory_filename(doc.title, doc.id)) if sub else (
        root / memory_filename(doc.title, doc.id)
    )
    return store.write(doc, dest=dest)


@pytest.mark.asyncio
async def test_teaching_and_manual_same_id_dedup(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    shared = _hex("a")
    _write(
        store,
        MemoryDoc(
            id=shared,
            type="teaching_case",
            title="列出列名",
            content="必须写列名，禁止星号 SELECT *",
        ),
    )
    _write(
        store,
        MemoryDoc(
            id=shared,
            type="gene_case",
            title="列出列名",
            content="必须写列名，禁止星号 SELECT *",
            payload={"error_signature": "select-star", "failure_type": "sql", "source": "manual"},
        ),
        sub="manual",
    )
    unique = _hex("b")
    _write(
        store,
        MemoryDoc(
            id=unique,
            type="gene_case",
            title="自动基因",
            content="查询必须显式列出列名，禁止星号",
            payload={"error_signature": "auto-star", "failure_type": "sql", "source": "automatic"},
        ),
        sub="cases",
    )
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="别把所有列一次查出来", project_id="local")
    teach_ids = [row["id"] for row in payload["teaching_cases"]]
    gene_ids = [row["id"] for row in payload["gene_cases"]]
    assert teach_ids.count(shared) == 1
    assert shared not in gene_ids
    assert unique in gene_ids
    retrieved = list(iter_events(store.rsi_dir))[-1]["retrieved"]
    assert retrieved.count(shared) == 1
    assert payload["hint"] == t("HINT_TEACH", "zh")


@pytest.mark.asyncio
async def test_oral_and_english_paraphrase_hits_gene_and_teaching(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    gene_id = _hex("c")
    teach_id = _hex("d")
    _write(
        store,
        MemoryDoc(
            id=gene_id,
            type="gene_case",
            title="宽表查询失败",
            content="查询必须显式列出列名，禁止星号",
            payload={
                "error_signature": "select-star",
                "failure_type": "sql",
                "solution": "列出列名",
            },
        ),
        sub="cases",
    )
    _write(
        store,
        MemoryDoc(
            id=teach_id,
            type="teaching_case",
            title="用户纠正星号",
            content="必须写列名，禁止 SELECT *",
            payload={"trigger": {"error_signature": "select-star", "failure_type": "sql"}},
        ),
    )
    svc = RecallService(store=store, feedback_secret="test")
    for task in ("别把所有列一次查出来", "don't select star from the table"):
        payload = await svc.recall(task=task, project_id="local")
        gene_ids = [row["id"] for row in payload["gene_cases"]]
        teach_ids = [row["id"] for row in payload["teaching_cases"]]
        assert gene_id in gene_ids
        assert teach_id in teach_ids
        assert all("error_signature" not in str(task) for _ in (1,))


@pytest.mark.asyncio
async def test_episode_fingerprint_from_events_without_yaml(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    event_id = _hex("e")
    append_event(store.rsi_dir, {
        "id": event_id,
        "ts": "2026-09-13T00:00:00Z",
        "kind": "recall",
        "task": "别把所有列一次查出来",
        "token": "tok-ep-1",
        "retrieved": [],
    })
    assert not list((store.rsi_dir / "memory" / "episodes").glob("*.yaml"))
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="don't select star", project_id="local")
    ids = [row["id"] for row in payload["episodes"]]
    assert event_id in ids
    assert not list((store.rsi_dir / "memory" / "episodes").glob("*.yaml"))


@pytest.mark.asyncio
async def test_episode_materializes_when_feedback_threshold_met(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    event_id = _hex("f")
    token = "tok-ep-rate"
    append_event(store.rsi_dir, {
        "id": event_id,
        "ts": "2026-09-13T00:00:00Z",
        "kind": "recall",
        "task": "别把所有列一次查出来",
        "token": token,
        "retrieved": [],
        "excerpt": "禁止 SELECT *",
    })
    append_event(store.rsi_dir, {
        "id": _hex("1"),
        "ts": "2026-09-13T00:01:00Z",
        "kind": "feedback",
        "token": token,
        "action": "accepted",
        "rating": 5,
        "comment": None,
    })
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="don't select star", project_id="local")
    assert payload["episodes"]
    yamls = list((store.rsi_dir / "memory" / "episodes").glob("*.yaml"))
    assert yamls
    assert any(row.get("source_path") for row in payload["episodes"])


@pytest.mark.asyncio
async def test_heading_from_section_on_recall_hit(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _hex("9")
    _write(
        store,
        MemoryDoc(
            id=doc_id,
            type="convention",
            title="查询约定",
            content="前言可忽略。\n\n## 列名规则\n必须写列名，禁止星号 SELECT *。",
        ),
    )
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="别把所有列一次查出来", project_id="local")
    hit = next(row for row in payload["items"] if row["id"] == doc_id)
    assert hit["heading"] == "列名规则"


def test_cache_wipe_reindex_top5_overlap(tmp_path, monkeypatch, capsys):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.rag.index import build_index, search

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
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
        docs.append(_write(
            store,
            MemoryDoc(
                id=f"{i + 1:08x}{'a' * 24}",
                type="prohibition",
                title=title,
                content=f"{title} 必须列字段",
            ),
        ))
    first = build_index(docs)
    assert run_memory(["reindex"]) == 0
    cache = tmp_path / ".rsi" / "cache"
    assert (cache / "inverted.json").is_file()
    shutil.rmtree(cache)
    assert not cache.exists()
    capsys.readouterr()
    assert run_memory(["reindex"]) == 0
    second = json.loads((cache / "inverted.json").read_text(encoding="utf-8"))
    query = "列名 星号 SELECT"
    top1 = [doc_id for doc_id, _ in search(first, query, top_n=5)]
    top2 = [doc_id for doc_id, _ in search(second, query, top_n=5)]
    union = set(top1) | set(top2)
    overlap = (len(set(top1) & set(top2)) / len(union)) if union else 1.0
    assert overlap >= 0.8
