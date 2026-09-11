"""rsi_recall 记忆召回测试（Spec v3.0 §4.3/§4.7，A1）：召回臂播种与 Thompson 选择、
禁止项置顶、召回事件落库（intent='recall'）、feedback_token 闭环。"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.log_service import LogService
from rsi_boot.services.recall_service import RecallService
from rsi_boot.strategy.recall import DEFAULT_ARMS, RecallArmSelector

SECRET = "test-secret"


def _services(db, config):
    retriever = KnowledgeRetriever(db, config)  # 无 embedding：纯 FTS5/bigram（v3.0 默认）
    arms = RecallArmSelector(db)
    knowledge = KnowledgeService(db, retriever)
    recall = RecallService(db, retriever, arms, SECRET)
    return recall, arms, knowledge


async def _add_item(db, knowledge: KnowledgeService, *, title, content,
                    content_type="convention", domain=None):
    return await knowledge.add(KnowledgeItem(
        project_id="p1", title=title, content=content,
        content_type=content_type, domain=domain, status="active",
    ))


# ---------- 召回臂 ----------


async def test_first_pick_seeds_default_arms(db, base_config):
    """项目首次召回播种三个默认臂（§4.7 参数表）"""
    _, arms, _ = _services(db, base_config)
    arm = await arms.pick("p1")
    assert arm.name in {name for name, _ in DEFAULT_ARMS}
    await asyncio.sleep(0.05)  # 曝光计数后台任务

    conn = await db.connect()
    async with conn.execute(
        "SELECT strategy_name, params, exposure_count FROM strategy_configs"
        " WHERE project_id = 'p1' AND intent = 'recall' ORDER BY strategy_name"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 3
    by_name = {r["strategy_name"]: r for r in rows}
    assert json.loads(by_name["recall-conservative"]["params"]) == {"top_n": 3, "threshold": 0.7}
    assert json.loads(by_name["recall-generous"]["params"]) == {"top_n": 8, "threshold": 0.5}
    # 曝光：被选中的臂 exposure_count = 1
    assert by_name[arm.name]["exposure_count"] == 1


async def test_arm_params_drive_recall(db, base_config):
    """召回臂 params 生效：conservative（top_n=3）限制返回条数"""
    _, arms, knowledge = _services(db, base_config)
    for i in range(6):
        await _add_item(db, knowledge, title=f"缓存经验{i}", content=f"缓存配置做法 {i} 容量 过期")
    # 只留 conservative 臂，消除 Thompson 随机性
    await arms.pick("p1")  # 触发播种
    conn = await db.connect()
    await conn.execute(
        "UPDATE strategy_configs SET is_active = 0 WHERE project_id = 'p1' AND intent = 'recall'"
        " AND strategy_name != 'recall-conservative'"
    )
    await conn.commit()
    arms.invalidate_cache()

    recall, _, _ = _services(db, base_config)
    recall._arms = arms
    result = await recall.recall("缓存配置怎么做", "p1")
    assert result["recall_arm"] == "recall-conservative"
    assert len(result["items"]) <= 3


# ---------- 禁止项置顶 ----------


async def test_prohibition_pinned_on_term_match(db, base_config):
    """禁止项词项命中（≥2）一律置顶，不走分数门槛"""
    recall, _, knowledge = _services(db, base_config)
    await _add_item(db, knowledge, title="禁止：不要用 SELECT *",
                    content="查询必须显式列出字段，禁止 SELECT *", content_type="prohibition",
                    domain="database")
    result = await recall.recall("不要用 SELECT * 查询用户表怎么写", "p1")
    assert [p["title"] for p in result["prohibitions"]] == ["禁止：不要用 SELECT *"]
    # 禁止项不重复出现在 items 通道
    assert all(i["content_type"] != "prohibition" for i in result["items"])


async def test_prohibition_no_match_below_min_hits(db, base_config):
    """单词项重叠不命中（防噪声）；无关任务不命中"""
    recall, _, knowledge = _services(db, base_config)
    await _add_item(db, knowledge, title="禁止：不要用 SELECT *",
                    content="查询必须显式列出字段", content_type="prohibition")
    result = await recall.recall("前端按钮样式调整", "p1")
    assert result["prohibitions"] == []


async def test_pending_review_prohibition_not_recalled(db, base_config):
    """仅 active 禁止项参与召回（审批闸门）"""
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?, 'p1', '禁止：不要用 SELECT *', '显式列字段',"
        " 'prohibition', 'pending_review', ?, ?)",
        (uuid.uuid4().hex, now, now),
    )
    await conn.commit()
    recall, _, _ = _services(db, base_config)
    result = await recall.recall("不要用 SELECT * 查询", "p1")
    assert result["prohibitions"] == []


# ---------- 事件落库与反馈闭环 ----------


async def test_recall_logs_event_and_token_verifiable(db, base_config):
    """每次召回落 intent='recall' 日志（token 恒 0），feedback_token 可正常反馈"""
    recall, _, knowledge = _services(db, base_config)
    await _add_item(db, knowledge, title="缓存经验", content="TTL 缓存容量 500")
    result = await recall.recall("缓存容量配置怎么做", "p1")  # bigram 命中 ≥2（缓存/容量）
    assert result["feedback_token"]

    conn = await db.connect()
    async with conn.execute(
        "SELECT intent, strategy_name, prompt_tokens, total_tokens, response_excerpt"
        " FROM interaction_logs WHERE feedback_token = ?",
        (result["feedback_token"],),
    ) as cur:
        row = await cur.fetchone()
    assert row["intent"] == "recall"
    assert row["strategy_name"].startswith("recall-")  # 召回臂名落 strategy_name
    assert row["prompt_tokens"] == 0 and row["total_tokens"] == 0  # 零模型调用
    assert "缓存经验" in row["response_excerpt"]  # 命中标题清单（高分召回 → 提取候选）

    # 反馈闭环：token 可回写（驱动召回臂学习与采纳率统计）
    logs = LogService(db)
    assert await logs.apply_feedback(result["feedback_token"], "accepted", 5) is True
    async with conn.execute(
        "SELECT feedback_action, feedback_rating FROM interaction_logs WHERE feedback_token = ?",
        (result["feedback_token"],),
    ) as cur:
        fb = await cur.fetchone()
    assert fb["feedback_action"] == "accepted" and fb["feedback_rating"] == 5


async def test_recall_top_k_override(db, base_config):
    """top_k 入参覆盖召回臂 top_n（上限 10）"""
    recall, _, knowledge = _services(db, base_config)
    for i in range(5):
        await _add_item(db, knowledge, title=f"缓存经验{i}", content=f"缓存做法 {i} 容量 过期")
    result = await recall.recall("缓存配置怎么做", "p1", top_k=2)
    assert len(result["items"]) <= 2
