"""rsi_conflicts list 分页 / run 过滤 + resolve 批量 decisions。"""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.injector.conflict import ConflictDetector


async def _item(db, project_id, title, source_url, tags, status="pending_review"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain,"
        " tags, source_url, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'convention', NULL, ?, ?, ?, ?, ?)",
        (item_id, project_id, title, f"{title} 正文 " * 6,
         json.dumps(tags, ensure_ascii=False), source_url, status, now, now),
    )
    await conn.commit()
    return item_id


async def _conflict(db, project_id, item_id, peer_source):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    cid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO rule_conflicts (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
        " user_rule_hash, conflict_type, status, resolution_note, detected_at)"
        " VALUES (?, ?, ?, ?, 'excerpt', ?, 'incoherent', 'open', 'recommended:coexist', ?)",
        (cid, project_id, item_id, peer_source, uuid.uuid4().hex, now),
    )
    await conn.commit()
    return cid


def _runtime(db):
    return SimpleNamespace(conflict_detector=ConflictDetector(db), project_id="p1")


async def test_list_filters_by_bootstrap_run_id(db):
    run_a, run_b = uuid.uuid4().hex, uuid.uuid4().hex
    item_a = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    item_b = await _item(db, "p1", "乙", "docs/b.md", ["signal:docs", f"bootstrap_run_id:{run_b}"])
    peer_a = await _item(db, "p1", "甲伴", "docs/a2.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    await _conflict(db, "p1", item_a, "docs/a2.md")   # 双侧 run A
    await _conflict(db, "p1", item_b, "docs/a2.md")   # item 侧 run B × peer 侧 run A → OR 口径两侧都算
    await _conflict(db, "p1", item_b, "docs/nowhere.md")  # 只 run B
    rt = _runtime(db)
    listed = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_a,
    })
    assert listed["status"] == "success"
    assert listed["data"]["count"] == 2
    listed_b = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_b,
    })
    assert listed_b["data"]["count"] == 2


async def test_list_paginates_with_limit_offset(db):
    item = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs"])
    for i in range(5):
        await _conflict(db, "p1", item, f"docs/peer{i}.md")
    rt = _runtime(db)
    page1 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 0})
    page2 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 2})
    assert page1["data"]["count"] == 2 and page2["data"]["count"] == 2
    ids1 = {c["id"] for c in page1["data"]["conflicts"]}
    ids2 = {c["id"] for c in page2["data"]["conflicts"]}
    assert not ids1 & ids2


async def test_list_pagination_stable_when_detected_at_ties(db):
    """同批 detected_at 并列时分页不得漏/重：ORDER BY 需 id 决胜键。"""
    item = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs"])
    conn = await db.connect()
    same_ts = "2026-09-13T00:00:00+00:00"
    for i in range(3):
        await conn.execute(
            "INSERT INTO rule_conflicts (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
            " user_rule_hash, conflict_type, status, resolution_note, detected_at)"
            " VALUES (?, ?, ?, ?, 'excerpt', ?, 'incoherent', 'open', 'recommended:coexist', ?)",
            (uuid.uuid4().hex, "p1", item, f"docs/tie{i}.md", uuid.uuid4().hex, same_ts),
        )
    await conn.commit()
    rt = _runtime(db)
    for _ in range(5):
        page1 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 0})
        page2 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 2})
        ids1 = {c["id"] for c in page1["data"]["conflicts"]}
        ids2 = {c["id"] for c in page2["data"]["conflicts"]}
        assert len(ids1 | ids2) == 3
        assert not ids1 & ids2
