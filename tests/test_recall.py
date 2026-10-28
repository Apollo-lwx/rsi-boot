"""rsi_recall 记忆召回测试（Spec v3.0 §4.3/§4.7，A1）：召回臂播种与 Thompson 选择、
禁止项置顶、召回事件落库（intent='recall'）、feedback_token 闭环。"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
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
    """未接召回臂时仍是 recall-balanced；top_k 限制返回条数"""
    for i in range(6):
        _write_official(store, title=f"缓存经验{i}", content=f"缓存配置做法 {i} 容量 过期")
    result = await _recall(store).recall("缓存配置怎么做", "p1", top_k=3)
    assert result["recall_arm"] == "recall-balanced"
    assert len(result["items"]) <= 3


async def test_cold_start_arm_is_balanced_and_caps_top_n(store):
    """没有反馈时不随机探索，条数用均衡臂的 5。"""
    for i in range(8):
        _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    service = RecallService(store=store, feedback_secret=SECRET, arms=RecallArmSelector(store=store))
    result = await service.recall("缓存配置做法", "p1")
    assert result["recall_arm"] == "recall-balanced"
    assert len(result["items"]) == 5


async def test_informed_arm_raises_top_n(store):
    """有奖励之后 Thompson 会选中慷慨臂，条数跟着变成 8。"""
    for i in range(8):
        _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    rows = []
    for name, params in DEFAULT_ARMS:
        alpha, beta = (1000.0, 1.0) if name == "recall-generous" else (1.0, 1000.0)
        rows.append({
            "id": uuid.uuid4().hex,
            "name": name,
            "intent": "recall",
            "top_n": params["top_n"],
            "threshold": params["threshold"],
            "alpha": alpha,
            "beta": beta,
            "active": True,
            "params": params,
        })
    path = store.rsi_dir / "state" / "arms.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    service = RecallService(
        store=store, feedback_secret=SECRET, arms=RecallArmSelector(store=store),
    )
    result = await service.recall("缓存配置做法", "p1")
    assert result["recall_arm"] == "recall-generous"
    assert len(result["items"]) == 8


async def test_index_is_reused_until_memory_changes(store, monkeypatch):
    _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    calls = {"n": 0}
    import rsi_boot.rag.index as index_mod

    real = index_mod.build_index

    def _wrapped(docs):
        calls["n"] += 1
        return real(docs)

    monkeypatch.setattr(index_mod, "build_index", _wrapped)
    service = _recall(store)
    await service.recall("缓存配置做法", "p1")
    await service.recall("缓存配置做法", "p1")
    assert calls["n"] == 1
    _write_official(store, title="另一条经验", content="日志轮转怎么做")
    await service.recall("缓存配置做法", "p1")
    assert calls["n"] == 2


async def test_repeated_feedback_breaks_a_lexical_tie(store):
    from rsi_boot.memory.logstore import append_event

    good = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    bad = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")

    def _vote(token: str, doc_id: str, action: str) -> None:
        append_event(store.rsi_dir, {
            "id": uuid.uuid4().hex, "ts": "2026-10-26T00:00:00Z", "kind": "recall",
            "token": token, "task": "缓存", "retrieved": [doc_id], "arm": "recall-balanced",
        })
        append_event(store.rsi_dir, {
            "id": uuid.uuid4().hex, "ts": "2026-10-26T00:00:01Z", "kind": "feedback",
            "token": token, "action": action, "rating": 5 if action == "accepted" else 1,
        })

    _vote("t-good-1", good, "accepted")
    _vote("t-good-2", good, "accepted")
    _vote("t-bad-1", bad, "rejected")
    _vote("t-bad-2", bad, "rejected")
    result = await _recall(store).recall("缓存配置做法", "p1")
    ids = [row["id"] for row in result["items"]]
    assert ids.index(good) < ids.index(bad)


def _stamp_feedback(store, doc_id: str, action: str, ts: str) -> None:
    from rsi_boot.memory.logstore import append_event

    token = uuid.uuid4().hex
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex, "ts": ts, "kind": "recall",
        "token": token, "task": "缓存", "retrieved": [doc_id], "arm": "recall-balanced",
    })
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex, "ts": ts, "kind": "feedback",
        "token": token, "action": action, "rating": 5 if action == "accepted" else 1,
    })


def test_few_votes_shrink_toward_neutral(store):
    """Beta(1,1) 先验：1 票信号 1/3，2 票全采纳信号 0.5，而不是打满 ±1。"""
    one = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    two = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    ts = datetime.now(timezone.utc).isoformat()
    _stamp_feedback(store, one, "accepted", ts)
    _stamp_feedback(store, two, "accepted", ts)
    _stamp_feedback(store, two, "accepted", ts)
    signals = _recall(store)._acceptance_signals(store)
    assert signals[one] == pytest.approx(1 / 3)
    assert signals[two] == pytest.approx(0.5)


def test_feedback_decays_with_profile_half_life(store):
    """与画像相同的 30 天半衰期：整段过期的采纳只剩一半权重。"""
    from rsi_boot.services.profile_service import HALF_LIFE_DAYS

    doc = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    old = (datetime.now(timezone.utc) - timedelta(days=HALF_LIFE_DAYS)).isoformat()
    _stamp_feedback(store, doc, "accepted", old)
    _stamp_feedback(store, doc, "accepted", old)
    signals = _recall(store)._acceptance_signals(store)
    assert signals[doc] == pytest.approx(1 / 3)


def test_acceptance_reads_older_roll(store):
    import json

    doc = _write_official(store, title="缓存经验", content="缓存配置做法 容量 过期")
    logs = store.rsi_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    token = "tok-old-roll"
    ts = datetime.now(timezone.utc).isoformat()
    old = [
        {"id": "old-recall", "ts": ts, "kind": "recall", "token": token, "retrieved": [doc], "task": "缓存"},
        {"id": "old-fb", "ts": ts, "kind": "feedback", "token": token, "action": "accepted", "rating": 5},
    ]
    (logs / "events-202001.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in old),
        encoding="utf-8",
    )
    (logs / "events-202608.jsonl").write_text(
        '{"id":"mid","kind":"recall","token":"mid","retrieved":[]}\n',
        encoding="utf-8",
    )
    signals = _recall(store)._acceptance_signals(store)
    assert signals[doc] == pytest.approx(1 / 3)


async def test_feedback_joins_the_arm_gate_on_one_score(store):
    """臂门槛、反馈和通道地板是同一个对数分上的一次取舍。

    只写了 rediscache 的条目词面大约是完整命中的一半，低于均衡臂的 0.6。
    这条线只按 0.2 的地板微调，采纳记在同一个分数里，它仍要留下来。
    """
    strong = _write_official(store, title="短", content="rediscache ttl session")
    weak = _write_official(store, title="长", content="rediscache")
    for i in range(4):
        _write_official(store, title=f"噪声{i}", content=f"ttl session pad{i}")
    ts = datetime.now(timezone.utc).isoformat()
    for _ in range(10):
        _stamp_feedback(store, weak, "accepted", ts)
    service = RecallService(
        store=store, feedback_secret=SECRET, arms=RecallArmSelector(store=store),
    )
    result = await service.recall("rediscache ttl session", "p1")
    ids = [row["id"] for row in result["items"]]
    assert strong in ids
    assert weak in ids
    assert ids.index(strong) < ids.index(weak)


def _open_health(store: MemoryStore, memory_id: str) -> None:
    root = store.rsi_dir / "state" / "memory_health"
    root.mkdir(parents=True, exist_ok=True)
    finding_id = uuid.uuid4().hex
    payload = {
        "id": finding_id,
        "memory_id": memory_id,
        "status": "open",
        "kind": "incorrect",
    }
    (root / f"{finding_id}.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


async def test_unhealthy_leader_does_not_lower_the_body_gate(store):
    """词面最高的一条有未处理健康问题时，淘汰线仍按纯正文。

    只命中一个词、正文被垫长的条目大约是完整命中的 0.13。
    健康度把参照分砍半时它会留下；参照分不含健康度时它过不了 0.2。
    """
    terms = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    strong = _write_official(store, title="完整", content=" ".join(terms))
    weak = _write_official(
        store,
        title="残缺",
        content="alpha " + " ".join(f"pad{i}" for i in range(8)),
    )
    for term in terms[1:]:
        _write_official(store, title="噪声", content=term)
    _open_health(store, strong)
    result = await _recall(store).recall(" ".join(terms), "p1", top_k=10)
    ids = [row["id"] for row in result["items"]]
    assert strong in ids
    assert weak not in ids


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


async def test_prohibition_common_bigram_alone_does_not_pin(store):
    """只共享「不要」这种高频 bigram 时不置顶；稀有词命中的禁止项仍然置顶。"""
    for i in range(12):
        _write_official(store, title=f"约定{i}", content=f"不要改这里 pad{i}")
    _write_official(
        store,
        title="禁止：不要打印密钥",
        content="不要把密钥写进日志",
        type="prohibition",
    )
    _write_official(
        store,
        title="禁止：不要改 rediscache",
        content="rediscache 的 ttl 不要自行缩短",
        type="prohibition",
    )
    result = await _recall(store).recall("不要改 rediscache 的 ttl", "p1")
    titles = [row["title"] for row in result["prohibitions"]]
    assert "禁止：不要打印密钥" not in titles
    assert "禁止：不要改 rediscache" in titles


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
    assert result["retrieved"] == retrieved
    assert low_id in result["retrieved"]
    assert high_id in result["retrieved"]


async def test_recall_explain_intent_does_not_pad_weak_jdbc_or_sql_explain(store):
    _write_official(
        store,
        title="AuthenticationType KERBEROS equals SPNEGO",
        content="On the remote JDBC client, KERBEROS and SPNEGO both send HTTP Authorization Negotiate",
    )
    _write_official(
        store,
        title="GaussDB EXPLAIN dual track",
        content="EXPLAIN ANALYZE is utility. EXPLAIN without ANALYZE is SqlExplain",
    )
    _write_official(
        store,
        title="Keep derived aliases out of JDBC column cache",
        content="JDBC cache must ignore derived alias columns",
    )
    _write_official(
        store,
        title="dm dual skip jdbc metadata",
        content="DUAL treated as a physical table; JDBC getColumns plus empty namespace fail-closed",
        type="gene_case",
    )
    _write_official(
        store,
        title="kerberos is real account login",
        content="Kerberos and SPNEGO are physical datasource accounts and must be rejected by default",
        type="teaching_case",
    )
    _write_official(
        store,
        title="Teaching: DM tree uses JDBC getSchemas",
        content="Dameng object-tree inventory must call official JDBC DatabaseMetaData.getSchemas",
        type="teaching_case",
    )
    result = await _recall(store).recall(
        "解释远端 JDBC 客户端上 Kerberos 与 SPNEGO 是否一回事，以及 Authorization 头如何发出",
        "p1",
        top_k=8,
    )
    item_titles = [row["title"] for row in result["items"]]
    assert "AuthenticationType KERBEROS equals SPNEGO" in item_titles
    assert "GaussDB EXPLAIN dual track" not in item_titles
    assert "Keep derived aliases out of JDBC column cache" not in item_titles
    assert "dm dual skip jdbc metadata" not in [row["title"] for row in result["gene_cases"]]
    teach_titles = [row["title"] for row in result["teaching_cases"]]
    assert "kerberos is real account login" in teach_titles
    assert "Teaching: DM tree uses JDBC getSchemas" not in teach_titles
