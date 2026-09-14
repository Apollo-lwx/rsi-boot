"""rsi_conflicts list 分页 / run 过滤 + resolve 批量 decisions。"""
import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.memory.paths import review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc

from memory_helpers import memory_conflict_rows, write_memory_conflict


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / ".rsi")


def _item(store: MemoryStore, title: str, source_url: str, tags: list[str]) -> str:
    item_id = uuid.uuid4().hex
    dest = review_dir(store.rsi_dir, "convention") / memory_filename(title, item_id)
    store.write(
        MemoryDoc(
            id=item_id,
            type="convention",
            title=title,
            content=f"{title} 正文 " * 6,
            tags=list(tags),
            extra={"source_url": source_url},
        ),
        dest=dest,
    )
    return item_id


def _conflict(
    store: MemoryStore,
    item_id: str,
    peer_source: str,
    *,
    detected_at: str | None = None,
    project_id: str = "p1",
) -> str:
    cid = write_memory_conflict(
        store, item_id=item_id, peer_source=peer_source,
        conflict_type="incoherent", resolution_note="recommended:coexist",
    )
    path = store.rsi_dir / "state" / "conflicts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("conflicts") if isinstance(data, dict) else data
    for row in rows or []:
        if row.get("id") == cid:
            row["project_id"] = project_id
            if detected_at:
                row["detected_at"] = detected_at
    path.write_text(
        yaml.safe_dump({"conflicts": rows}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cid


def _runtime(store: MemoryStore, tmp_path: Path):
    return SimpleNamespace(
        conflict_detector=ConflictDetector(store=store, project_root=tmp_path),
        project_id="p1",
    )


async def test_list_filters_by_bootstrap_run_id(tmp_path):
    store = _store(tmp_path)
    run_a, run_b = uuid.uuid4().hex, uuid.uuid4().hex
    item_a = _item(store, "甲", "docs/a.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    item_b = _item(store, "乙", "docs/b.md", ["signal:docs", f"bootstrap_run_id:{run_b}"])
    _item(store, "甲伴", "docs/a2.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    _conflict(store, item_a, "docs/a2.md")   # 双侧 run A
    _conflict(store, item_b, "docs/a2.md")   # item 侧 run B × peer 侧 run A → OR 口径两侧都算
    _conflict(store, item_b, "docs/nowhere.md")  # 只 run B
    rt = _runtime(store, tmp_path)
    listed = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_a,
    })
    assert listed["status"] == "success"
    assert listed["data"]["count"] == 2
    listed_b = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_b,
    })
    assert listed_b["data"]["count"] == 2


async def test_list_paginates_with_limit_offset(tmp_path):
    store = _store(tmp_path)
    item = _item(store, "甲", "docs/a.md", ["signal:docs"])
    for i in range(5):
        _conflict(store, item, f"docs/peer{i}.md")
    rt = _runtime(store, tmp_path)
    page1 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 0})
    page2 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 2})
    assert page1["data"]["count"] == 2 and page2["data"]["count"] == 2
    ids1 = {c["id"] for c in page1["data"]["conflicts"]}
    ids2 = {c["id"] for c in page2["data"]["conflicts"]}
    assert not ids1 & ids2


async def test_list_pagination_stable_when_detected_at_ties(tmp_path):
    """同批 detected_at 并列时分页不得漏/重：ORDER BY 需 id 决胜键。"""
    store = _store(tmp_path)
    item = _item(store, "甲", "docs/a.md", ["signal:docs"])
    same_ts = "2026-09-13T00:00:00+00:00"
    for i in range(3):
        _conflict(store, item, f"docs/tie{i}.md", detected_at=same_ts)
    rt = _runtime(store, tmp_path)
    for _ in range(5):
        page1 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 0})
        page2 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 2})
        ids1 = {c["id"] for c in page1["data"]["conflicts"]}
        ids2 = {c["id"] for c in page2["data"]["conflicts"]}
        assert len(ids1 | ids2) == 3
        assert not ids1 & ids2


async def test_resolve_decisions_batch_partial_failure_no_rollback(tmp_path):
    store = _store(tmp_path)
    item = _item(store, "甲", "docs/a.md", ["signal:docs"])
    ok1 = _conflict(store, item, "docs/p1.md")
    ok2 = _conflict(store, item, "docs/p2.md")
    rt = _runtime(store, tmp_path)
    result = await conflicts_tool.handle(rt, {
        "action": "resolve",
        "decisions": [
            {"conflict_id": ok1, "resolution": "coexist"},
            {"conflict_id": "不存在的id", "resolution": "coexist"},
            {"conflict_id": ok2, "resolution": "keep_item"},
        ],
    })
    assert result["status"] == "success"
    data = result["data"]
    assert {r["conflict_id"] for r in data["resolved"]} == {ok1, ok2}
    assert [f["conflict_id"] for f in data["failed"]] == ["不存在的id"]
    # 已成功的不回滚
    row = next(r for r in memory_conflict_rows(tmp_path) if r["id"] == ok1)
    assert row["status"] == "coexist"


async def test_resolve_decisions_over_200_rejected_without_executing(tmp_path):
    store = _store(tmp_path)
    item = _item(store, "甲", "docs/a.md", ["signal:docs"])
    cid = _conflict(store, item, "docs/p1.md")
    rt = _runtime(store, tmp_path)
    result = await conflicts_tool.handle(rt, {
        "action": "resolve",
        "decisions": [{"conflict_id": cid, "resolution": "coexist"}] * 201,
    })
    assert result["status"] == "error"
    assert "200" in result["message"]
    row = next(r for r in memory_conflict_rows(tmp_path) if r["id"] == cid)
    assert row["status"] == "open"  # 一条都没执行
