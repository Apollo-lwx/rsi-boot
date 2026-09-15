"""rsi_recall 记忆召回测试（Spec v3.0 §4.3/§4.7，A1）：召回臂播种与 Thompson 选择、
禁止项置顶、召回事件落库（intent='recall'）、feedback_token 闭环。"""

import asyncio
import uuid
from pathlib import Path

import yaml

from rsi_boot.memory.logstore import iter_events
from rsi_boot.memory.paths import official_dir, review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.log_service import LogService
from rsi_boot.services.recall_service import RecallService
from rsi_boot.strategy.recall import DEFAULT_ARMS, RecallArmSelector

SECRET = "test-secret"


def _recall(store: MemoryStore) -> RecallService:
    return RecallService(store=store, feedback_secret=SECRET)


def _write_official(store: MemoryStore, *, title, content, type="convention", domain=None) -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(
        MemoryDoc(id=item_id, type=type, title=title, content=content, domain=domain),
        dest=dest,
    )
    return item_id


def _arm_rows(store: MemoryStore) -> list[dict]:
    path = store.rsi_dir / "state" / "arms.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else list((payload or {}).get("arms") or [])


# ---------- 召回臂 ----------


async def test_first_pick_seeds_default_arms(store):
    """项目首次召回播种三个默认臂（§4.7 参数表）"""
    arms = RecallArmSelector(store=store)
    arm = await arms.pick("p1")
    assert arm.name in {name for name, _ in DEFAULT_ARMS}
    await asyncio.sleep(0.05)  # 曝光计数后台任务

    rows = _arm_rows(store)
    assert len(rows) == 3
    by_name = {r.get("name") or r.get("strategy_name"): r for r in rows}
    assert by_name["recall-conservative"]["params"] == {"top_n": 3, "threshold": 0.7}
    assert by_name["recall-generous"]["params"] == {"top_n": 8, "threshold": 0.5}
    picked = by_name[arm.name]
    assert int(picked.get("exposure") or picked.get("exposure_count") or 0) == 1


async def test_arm_params_drive_recall(store):
    """文件运行时召回臂固定 recall-balanced；top_k 仍限制返回条数"""
    for i in range(6):
        _write_official(store, title=f"缓存经验{i}", content=f"缓存配置做法 {i} 容量 过期")
    result = await _recall(store).recall("缓存配置怎么做", "p1", top_k=3)
    assert result["recall_arm"] == "recall-balanced"
    assert len(result["items"]) <= 3


# ---------- 禁止项置顶 ----------


async def test_prohibition_pinned_on_term_match(store):
    """禁止项命中一律置顶，不走分数门槛"""
    _write_official(
        store, title="禁止：不要用 SELECT *",
        content="查询必须显式列出字段，禁止 SELECT *", type="prohibition",
        domain="database",
    )
    result = await _recall(store).recall("不要用 SELECT * 查询用户表怎么写", "p1")
    assert [p["title"] for p in result["prohibitions"]] == ["禁止：不要用 SELECT *"]
    assert all(i["content_type"] != "prohibition" for i in result["items"])


async def test_prohibition_no_match_below_min_hits(store):
    """无关任务不命中禁止项"""
    _write_official(
        store, title="禁止：不要用 SELECT *",
        content="查询必须显式列出字段", type="prohibition",
    )
    result = await _recall(store).recall("前端按钮样式调整", "p1")
    assert result["prohibitions"] == []


async def test_pending_review_prohibition_not_recalled(store):
    """仅 official/active 禁止项参与召回（审批闸门）"""
    item_id = uuid.uuid4().hex
    title = "禁止：不要用 SELECT *"
    dest = review_dir(store.rsi_dir, "prohibition") / memory_filename(title, item_id)
    store.write(
        MemoryDoc(id=item_id, type="prohibition", title=title, content="显式列字段"),
        dest=dest,
    )
    result = await _recall(store).recall("不要用 SELECT * 查询", "p1")
    assert result["prohibitions"] == []


# ---------- 事件落库与反馈闭环 ----------


async def test_recall_logs_event_and_token_verifiable(store):
    """每次召回落 kind='recall' 事件，feedback_token 可正常反馈"""
    _write_official(store, title="缓存经验", content="TTL 缓存容量 500")
    result = await _recall(store).recall("缓存容量配置怎么做", "p1")
    assert result["feedback_token"]

    row = [e for e in iter_events(store.rsi_dir) if e.get("kind") == "recall"][-1]
    assert row["token"] == result["feedback_token"]
    assert str(row.get("arm") or result["recall_arm"]).startswith("recall-")
    assert "缓存经验" in (row.get("excerpt") or "")

    logs = LogService(store)
    assert await logs.apply_feedback(result["feedback_token"], "accepted", 5) is True
    fb = [e for e in iter_events(store.rsi_dir) if e.get("kind") == "feedback"][-1]
    assert fb["action"] == "accepted" and fb["rating"] == 5


async def test_recall_top_k_override(store):
    """top_k 入参覆盖默认 top_n（上限 10）"""
    for i in range(5):
        _write_official(store, title=f"缓存经验{i}", content=f"缓存做法 {i} 容量 过期")
    result = await _recall(store).recall("缓存配置怎么做", "p1", top_k=2)
    assert len(result["items"]) <= 2


async def test_skill_catalog_is_memory_store_only(store):
    from rsi_boot.scanner.reading_packs import write_relearn_skill

    assert write_relearn_skill(store) is True
    root = store.rsi_dir.parent
    for rel in (
        Path(".cursor") / "skills" / "ghost" / "SKILL.md",
        Path(".claude") / "skills" / "ghost" / "SKILL.md",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Ghost\n\nNot a memory skill.\n", encoding="utf-8")
    result = await _recall(store).recall("重新学习项目知识", "p1")
    names = [item.get("name") for item in result["skills"]]
    assert "rsi-relearn" in names
    assert "ghost" not in names


async def test_recall_suggestions_and_retrieved_case_ids(store):
    low_id = uuid.uuid4().hex
    high_id = uuid.uuid4().hex
    harvest_id = uuid.uuid4().hex
    store.write(
        MemoryDoc(
            id=low_id,
            type="teaching_case",
            title="首次修 SELECT 星号",
            content="必须写列名，禁止 SELECT *，首次修复只作建议",
            payload={
                "lesson": {"author": "agent", "wrong_action": "SELECT *", "correct_fix": "列名"},
                "fix_and_learn": {"gene_map_weight": 2},
            },
        ),
        dest=official_dir(store.rsi_dir, "teaching_case") / memory_filename("首次修 SELECT 星号", low_id),
    )
    store.write(
        MemoryDoc(
            id=high_id,
            type="teaching_case",
            title="用户纠正 SELECT 星号",
            content="必须写列名，禁止 SELECT *，用户纠正后作为约束",
            payload={
                "lesson": {"author": "user", "wrong_action": "SELECT *", "correct_fix": "列名"},
                "fix_and_learn": {"gene_map_weight": 10},
            },
        ),
        dest=official_dir(store.rsi_dir, "teaching_case") / memory_filename("用户纠正 SELECT 星号", high_id),
    )
    store.write(
        MemoryDoc(
            id=harvest_id,
            type="documentation",
            title="代码骨架摘要",
            content="class User 骨架足够长用于检索 SELECT",
            tags=["signal:code"],
        ),
        dest=official_dir(store.rsi_dir, "documentation") / memory_filename("代码骨架摘要", harvest_id),
    )
    result = await _recall(store).recall("禁止 SELECT * 怎么写列名", "p1")
    by_id = {row["id"]: row for row in result["teaching_cases"]}
    assert low_id in by_id
    assert by_id[low_id]["role"] == "suggestion"
    assert by_id[low_id]["weight"] == 2
    assert high_id in by_id
    assert by_id[high_id]["role"] == "constraint"
    assert by_id[high_id]["weight"] == 10
    suggestion_ids = [row["id"] for row in result["suggestions"]]
    assert low_id in suggestion_ids
    assert high_id not in suggestion_ids
    assert low_id not in [row["id"] for row in result["prohibitions"]]
    assert harvest_id not in [row["id"] for row in result["items"]]
    retrieved = [e for e in iter_events(store.rsi_dir) if e.get("kind") == "recall"][-1]["retrieved"]
    assert low_id in retrieved
    assert high_id in retrieved
