"""记忆层四项改进的回归测试（对标 Qoder 记忆机制的补齐）。

覆盖：
1. 轻量记忆目录被动注入（AGENTS.md + Cursor rsi-catalog.mdc）
2. 收工纪律强制 rsi_feedback 采纳上报
3. 写入 / 晋升时的记忆间近重复检测
4. MemoryDoc 前提 / 失效条件在召回中回传宿主
"""

from __future__ import annotations

import uuid

import pytest

from rsi_boot.api.tools import knowledge_add_tool
from rsi_boot.api.tools import knowledge_review_tool
from rsi_boot.api.tools import explore_tool
from rsi_boot.injector.rule_injector import RuleInjector
from rsi_boot.injector.targets import AgentsMdTarget, MemoryBundle
from rsi_boot.memory.paths import official_dir, review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.recall_service import RecallService

SECRET = "test-secret"


def _write_official(store, *, title, content, type="convention", **kw) -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(
        MemoryDoc(id=item_id, type=type, title=title, content=content, **kw), dest=dest,
    )
    return item_id


# ---------- 改进 1：轻量记忆目录被动注入 ----------


async def test_catalog_injected_passively(store, tmp_path):
    """正式约定正文不进宿主上下文，但标题+简述进目录（AGENTS.md 与 rsi-catalog.mdc）"""
    _write_official(
        store, title="统一使用 snake_case",
        content="变量与函数命名规范说明：" + ("填充内容" * 60) + "TAILMARKER",
    )
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite("p1")

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "记忆目录" in agents
    assert "统一使用 snake_case" in agents
    # 正文细节（超出简述预算的尾部）不进目录，详情走 rsi_recall
    assert "TAILMARKER" not in agents
    assert (tmp_path / ".cursor" / "rules" / "rsi-catalog.mdc").is_file()


async def test_catalog_empty_writes_no_catalog_file(store, tmp_path):
    """无正式约定/文档时不产出 rsi-catalog.mdc（不改变原有注入产物集合）"""
    _write_official(store, title="禁止 SELECT *", content="必须显式列字段", type="prohibition")
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite("p1")
    assert list((tmp_path / ".cursor" / "rules").glob("rsi-catalog.mdc")) == []


# ---------- 改进 2：收工纪律强制 rsi_feedback 采纳上报 ----------


def test_closeout_requires_feedback(tmp_path):
    AgentsMdTarget(tmp_path).write(MemoryBundle())
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "rsi_feedback" in agents
    assert "accepted 或 rejected" in agents


# ---------- 改进 3：近重复检测 ----------


async def test_add_reports_near_duplicates(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    # source_url 使 add 落正式（active），供近邻扫描命中
    r1 = await knowledge_add_tool.handle(svc, {
        "title": "命名约定", "content": "本项目统一使用 snake_case 命名变量和函数",
        "content_type": "convention", "source_url": "docs/style.md",
    })
    assert r1["status"] == "success"
    r2 = await knowledge_add_tool.handle(svc, {
        "title": "命名约定", "content": "本项目统一使用 snake_case 命名变量和函数哦",
        "content_type": "convention", "source_url": "docs/style2.md",
    })
    assert r2["status"] == "success"
    dups = r2.get("possible_duplicates") or []
    assert any(d["id"] == r1["id"] for d in dups)


async def test_add_unrelated_no_duplicates(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    await knowledge_add_tool.handle(svc, {
        "title": "数据库分页", "content": "深分页必须用键集游标，禁用 OFFSET",
        "content_type": "convention", "source_url": "docs/db.md",
    })
    r2 = await knowledge_add_tool.handle(svc, {
        "title": "前端构建", "content": "打包产物走 CDN，publicPath 需为绝对 URL",
        "content_type": "convention", "source_url": "docs/fe.md",
    })
    assert not r2.get("possible_duplicates")


def test_shared_boilerplate_is_not_a_near_duplicate(store):
    """语料里反复出现的套话权重要褪掉；只共享套话不算近重复，原文再写一遍仍然算。"""
    from rsi_boot.learning.dedup import build_promotion_pool, find_near_duplicates, pool_check

    template = "本项目约定如下请遵守执行不要改动默认配置"
    for i in range(12):
        _write_official(store, title=f"约定{i}", content=f"{template} pad{i}")
    kept = _write_official(store, title="缓存", content=f"{template} rediscache")
    boilerplate = find_near_duplicates(
        store, title="数据库", content=f"{template} postgresdb", content_type="convention",
    )
    assert boilerplate == []
    copied = find_near_duplicates(
        store, title="缓存", content=f"{template} rediscache", content_type="convention",
    )
    assert any(row["id"] == kept for row in copied)
    probe = MemoryDoc(
        id=uuid.uuid4().hex, type="convention", title="数据库", content=f"{template} postgresdb",
    )
    assert pool_check(build_promotion_pool(store), probe) == []


# ---------- 改进 4：前提 / 失效条件在召回中回传 ----------


async def test_recall_surfaces_condition(store):
    _write_official(
        store,
        title="redis ttl session cache",
        content="set redis ttl to 300 seconds for session cache entries",
        type="convention",
        trigger_condition="仅当使用 Redis 缓存会话时",
        expiry_condition="迁移到集中式缓存后作废",
    )
    svc = RecallService(store=store, feedback_secret=SECRET)
    payload = await svc.recall("redis ttl session cache policy", "p1")
    items = payload["items"]
    assert items
    matched = next((i for i in items if i.get("condition")), None)
    assert matched is not None
    assert matched["condition"]["must_hold"] == "仅当使用 Redis 缓存会话时"
    assert matched["condition"]["may_expire_when"] == "迁移到集中式缓存后作废"


async def test_recall_no_condition_omits_key(store):
    _write_official(
        store, title="plain cache rule", content="cache entries expire after five minutes",
        type="convention",
    )
    svc = RecallService(store=store, feedback_secret=SECRET)
    payload = await svc.recall("plain cache rule expire five minutes", "p1")
    for i in payload["items"]:
        assert "condition" not in i


# ---------- schema 校验：旧条目缺字段仍可读取 ----------


def test_legacy_doc_without_conditions_validates(store):
    item_id = _write_official(store, title="old", content="legacy entry", type="convention")
    doc = store.read(item_id)
    assert doc.trigger_condition is None
    assert doc.expiry_condition is None


@pytest.mark.parametrize("bad", [dict(trigger_condition=123)])
def test_condition_must_be_str(store, bad):
    with pytest.raises(Exception):
        _write_official(store, title="x", content="y", type="convention", **bad)


# ---------- 改进 3 强约束版：block_on_promote 晋升前拦截近重复 ----------


class _Rt:
    """伪造 runtime：review tool 需 .knowledge/.store/.decisions/.config。"""

    def __init__(self, store, knowledge, config):
        self.store = store
        self.knowledge = knowledge
        self.decisions = None
        self.config = config


def _write_pending(store, *, title, content, type="convention") -> str:
    item_id = uuid.uuid4().hex
    dest = review_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(MemoryDoc(id=item_id, type=type, title=title, content=content), dest=dest)
    return item_id


async def test_promote_blocked_when_flag_on(store):
    _write_official(store, title="命名约定", content="本项目统一使用 snake_case 命名变量和函数哦")
    pid = _write_pending(store, title="命名规范", content="本项目统一使用 snake_case 命名变量和函数哦")
    svc = KnowledgeService(store=store)
    rt = _Rt(store, svc, {"dedup": {"block_on_promote": True}})
    out = await knowledge_review_tool.handle(rt, {"action": "approve", "id": pid})
    assert out.get("requires_adjudication") is True
    assert out.get("approved") is False
    assert out.get("possible_duplicates")
    # 命中近重复则暂缓：仍留在待审，未写第二条正式记忆
    assert svc._store.read(pid).status == "pending_review"


async def test_promote_not_blocked_when_flag_off(store):
    _write_official(store, title="命名约定", content="本项目统一使用 snake_case 命名变量和函数哦")
    pid = _write_pending(store, title="命名规范", content="本项目统一使用 snake_case 命名变量和函数哦")
    svc = KnowledgeService(store=store)
    rt = _Rt(store, svc, {})  # 无 dedup 配置 → 默认不阻断
    out = await knowledge_review_tool.handle(rt, {"action": "approve", "id": pid})
    assert out["new_status"] == "active"
    assert out.get("possible_duplicates")  # 非阻断仍回传候选
    assert svc._store.read(pid).status == "active"


async def test_promote_ids_partition_blocked(store):
    _write_official(store, title="命名约定", content="本项目统一使用 snake_case 命名变量和函数哦")
    dup = _write_pending(store, title="命名规范", content="本项目统一使用 snake_case 命名变量和函数哦")
    uniq = _write_pending(store, title="深分页", content="深分页必须用键集游标，禁用 OFFSET")
    svc = KnowledgeService(store=store)
    rt = _Rt(store, svc, {"dedup": {"block_on_promote": True}})
    out = await knowledge_review_tool.handle(rt, {"action": "approve", "ids": [dup, uniq]})
    assert out["processed"] == 1
    deferred = out.get("deferred_duplicates") or []
    assert any(d["id"] == dup for d in deferred)
    assert svc._store.read(dup).status == "pending_review"
    assert svc._store.read(uniq).status == "active"


# ---------- 架构缺口 #1：层级知识树（被动注入 + rsi_explore 下钻） ----------


def _seed_domain_docs(store):
    _write_official(store, title="接口鉴权", content="x", type="convention", domain="backend/api")
    _write_official(store, title="分页规范", content="y", type="convention", domain="backend/db")
    _write_official(store, title="索引选择", content="z", type="convention", domain="backend/db")
    _write_official(store, title="构建产物", content="w", type="convention", domain="frontend")


def test_tree_build_counts_hierarchy(store):
    from rsi_boot.memory.tree import build_tree, find_node

    _seed_domain_docs(store)
    docs = [d for t in ("convention",) for d in store.list_official(t) if d.status == "active"]
    root = build_tree(docs)
    backend = find_node(root, "backend")
    assert backend is not None and backend.total == 3
    assert find_node(root, "frontend").total == 1
    assert find_node(root, "backend/db").direct == 2
    assert find_node(root, "nope") is None


async def test_tree_injected_passively(store, tmp_path):
    _seed_domain_docs(store)
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite("p1")

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "知识树" in agents
    assert "backend (3)" in agents
    # 顶层板块附下钻提示
    assert 'rsi_explore "backend"' in agents
    assert (tmp_path / ".cursor" / "rules" / "rsi-tree.mdc").is_file()


async def test_tree_empty_writes_no_tree_file(store, tmp_path):
    _write_official(store, title="禁止 SELECT *", content="显式列字段", type="prohibition")
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite("p1")
    assert list((tmp_path / ".cursor" / "rules").glob("rsi-tree.mdc")) == []


async def test_explore_top_and_drilldown(store):
    _seed_domain_docs(store)
    rt = _Rt(store, None, {})
    top = await explore_tool.handle(rt, {})
    names = {c["name"]: c["total"] for c in top["data"]["children"]}
    assert names.get("backend") == 3
    assert names.get("frontend") == 1

    deep = await explore_tool.handle(rt, {"path": "backend"})
    data = deep["data"]
    assert data["exists"] is True and data["total"] == 3
    assert {c["name"] for c in data["children"]} == {"api", "db"}
    titles = {i["title"] for i in data["items"]}
    assert titles == {"接口鉴权", "分页规范", "索引选择"}


async def test_explore_unknown_path_falls_back(store):
    _seed_domain_docs(store)
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "nope/xyz"})
    assert out["data"]["exists"] is False
    assert out["data"]["children"]  # 回落给顶层板块供继续浏览


# ---------- 架构缺口 #1 阶段2：写入自动补 domain 层级 ----------

@pytest.mark.parametrize("path,expect", [
    ("docs/db.md", "docs"),
    ("backend/api/router.py", "backend"),
    ("README.md", "README"),
    ("signal:code", ""),
    ("https://example.com/a", ""),
    ("", ""),
    # 阶段 2 review I1：绝对路径未传 root 时不污染板块（不返回 "C:"/"根"）
    ("C:/Users/me/proj/docs/a.md", ""),
    ("D:\\project\\docs\\a.md", ""),
    ("/abs/docs/a.md", ""),
    ("..", ""),
    ("../outside/x.md", ""),
])
def test_infer_domain(path, expect):
    from rsi_boot.scanner.reading_packs import infer_domain
    assert infer_domain(path) == expect


def test_infer_domain_absolute_relative_to_root(tmp_path):
    from rsi_boot.scanner.reading_packs import infer_domain
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "a.md"
    target.write_text("x", encoding="utf-8")
    # 传 root → 相对化后按 _doc_domain 取域
    assert infer_domain(str(target), root=tmp_path) == "docs"
    # 越界 root 之外 → 放弃
    assert infer_domain("C:/elsewhere/x/a.md", root=tmp_path) == ""


async def test_add_auto_fills_domain_from_source(tmp_path):
    from rsi_boot.memory.tree import build_tree, find_node
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    r = await knowledge_add_tool.handle(svc, {
        "title": "分页规范", "content": "列表接口统一 page 从 1 开始，size 默认 20",
        "source_url": "backend/api/page.md",   # 宿主未给 domain
    })
    doc = store.read(r["id"])
    assert doc.domain == "backend"             # 自动补板块域，进知识树而非 general
    root = build_tree([doc])
    assert find_node(root, "backend") is not None


async def test_add_keeps_explicit_domain(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    r = await knowledge_add_tool.handle(svc, {
        "title": "手写约定", "content": "显式域不应被来源路径覆盖",
        "domain": "manual/deep", "source_url": "docs/x.md",
    })
    assert store.read(r["id"]).domain == "manual/deep"


# ---------- 架构缺口 #2：图谱边并入 rsi_explore ----------


async def test_explore_returns_graph_edges(store):
    from rsi_boot.memory.graph import add_edge
    b_id = _write_official(store, title="索引选择", content="y", type="convention", domain="backend")
    a_id = _write_official(
        store, title="分页规范", content="x", type="convention",
        domain="backend", refs=[b_id],
    )
    add_edge(store.rsi_dir, a_id, b_id, "supersedes")
    add_edge(store.rsi_dir, a_id, b_id, "conflicts")
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "backend"})
    data = out["data"]
    rel_pairs = {(e["rel"], e["id"]) for e in data["related"]}
    assert ("supersedes", b_id) in rel_pairs
    assert ("cites", b_id) in rel_pairs  # 从 refs 合成
    assert any(e["rel"] == "conflicts" and e["id"] == b_id for e in data["conflicts"])
    # 方向：a 是 anchor，b 是 other，out
    entry = next(e for e in data["related"] if e["rel"] == "supersedes")
    assert entry["direction"] == "out"
    assert entry["anchor_id"] == a_id


async def test_explore_top_level_omits_edges(store):
    from rsi_boot.memory.graph import add_edge
    a_id = _write_official(store, title="A", content="a", type="convention", domain="backend")
    b_id = _write_official(store, title="B", content="b", type="convention", domain="backend")
    add_edge(store.rsi_dir, a_id, b_id, "supersedes")
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {})  # 顶层概览
    assert "related" not in out["data"]
    assert "conflicts" not in out["data"]


async def test_explore_edges_fail_open_on_missing_endpoint(store):
    from rsi_boot.memory.graph import add_edge
    a_id = _write_official(store, title="A", content="a", type="convention", domain="backend")
    add_edge(store.rsi_dir, a_id, "ghostid", "supersedes")  # 端点不存在
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "backend"})
    # 不抛，且 ghost 边被忽略
    assert all(e["id"] != "ghostid" for e in out["data"]["related"])


async def test_explore_edges_dedup_same_rel(store):
    """同一 (rel, anchor, other) 只回传一条，避免 refs 里重复 id 或
    catalog 与 refs 同 rel 双写导致 20 槽被重复吃掉。"""
    b_id = _write_official(store, title="B", content="b", type="convention", domain="backend")
    _write_official(
        store, title="A", content="a", type="convention",
        domain="backend", refs=[b_id, b_id],   # 制造重复 cites
    )
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "backend"})
    cites = [e for e in out["data"]["related"] if e["rel"] == "cites"]
    assert len(cites) == 1


async def test_explore_edges_drop_pending_endpoint(store):
    """未审批（pending）记忆标题不得经 related 泄漏给宿主。"""
    from rsi_boot.memory.paths import pending_dir
    b_pending = uuid.uuid4().hex
    dest = pending_dir(store.rsi_dir, "convention") / memory_filename("PendingTitle", b_pending)
    store.write(
        MemoryDoc(id=b_pending, type="convention", title="PendingTitle",
                  content="p", domain="backend"),
        dest=dest,
    )
    # active 的 A refs 指向 pending 的 B：端点非 active → 边丢弃
    _write_official(
        store, title="A", content="a", type="convention",
        domain="backend", refs=[b_pending],
    )
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "backend"})
    assert all(e["id"] != b_pending for e in out["data"]["related"])
    assert all("PendingTitle" != e["title"] for e in out["data"]["related"])


async def test_explore_slash_path_treated_as_top(store):
    """S1："/"、"//"、"\\" 都归一为空 → 走顶层分支，不扫图谱、data 无 related/conflicts。"""
    from rsi_boot.memory.graph import add_edge
    a_id = _write_official(store, title="A", content="a", type="convention", domain="backend")
    b_id = _write_official(store, title="B", content="b", type="convention", domain="backend")
    add_edge(store.rsi_dir, a_id, b_id, "supersedes")
    rt = _Rt(store, None, {})
    for weird in ("/", "//", "\\", " / "):
        out = await explore_tool.handle(rt, {"path": weird})
        assert "related" not in out["data"], weird
        assert "conflicts" not in out["data"], weird


# ---------- 治理轮 S3：条件字段/来源/域长度护栏 ----------


async def test_add_rejects_oversize_trigger(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    r = await knowledge_add_tool.handle(svc, {
        "title": "T", "content": "y",
        "trigger_condition": "a" * 600,
    })
    assert r["status"] == "error"
    assert r.get("code") == "invalid"


async def test_add_rejects_oversize_source_url(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    r = await knowledge_add_tool.handle(svc, {
        "title": "T", "content": "y",
        "source_url": "https://x/" + "a" * 600,
    })
    assert r["status"] == "error"


async def test_add_rejects_oversize_domain(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    r = await knowledge_add_tool.handle(svc, {
        "title": "T", "content": "y", "domain": "d" * 300,
    })
    assert r["status"] == "error"


# ---------- 治理轮 S4：catalog 每类保底 1 席 + 时间倒序 ----------


async def test_catalog_seeds_each_type(store, tmp_path):
    """50 条旧 documentation 曾把 cap=40 全占满；teaching/gene/episode
    新条目应各至少拿 1 席（治理轮 S4）。"""
    for i in range(50):
        _write_official(
            store, title=f"D{i:03d}", content="x",
            type="documentation", domain="docs",
            updated_at=f"2020-01-{(i % 28) + 1:02d}T00:00:00Z",
        )
    for typ, title in (
        ("teaching_case", "T1"),
        ("gene_case", "G1"),
        ("episode", "E1"),
    ):
        _write_official(
            store, title=title, content="y",
            type=typ, domain=typ, updated_at="2026-10-01T00:00:00Z",
        )
    inj = RuleInjector(store=store, project_root=tmp_path)
    rows, _tree = inj._load_catalog_and_tree()
    types_in_rows = {r.content_type for r in rows}
    assert "teaching_case" in types_in_rows
    assert "gene_case" in types_in_rows
    assert "episode" in types_in_rows
    # 每类保底 1 席之外，剩余槽应按 updated_at 倒序（新文档挤旧文档）
    doc_rows = [r for r in rows if r.content_type == "documentation"]
    assert all(r.title.startswith("D") for r in doc_rows)


# ---------- 治理轮 S2：explore 图谱边 truncated 标记 ----------


async def test_explore_marks_truncated(store):
    from rsi_boot.memory.graph import add_edge
    anchor = _write_official(store, title="A", content="a", type="convention", domain="backend")
    # 21 条 supersedes 边 → 触发 _MAX_EDGES_PER_BUCKET=20 截断
    for i in range(21):
        other = _write_official(
            store, title=f"B{i:02d}", content=f"b{i}",
            type="convention", domain="backend",
        )
        add_edge(store.rsi_dir, anchor, other, "supersedes")
    rt = _Rt(store, None, {})
    out = await explore_tool.handle(rt, {"path": "backend"})
    assert len(out["data"]["related"]) == 20
    assert out["data"].get("related_truncated") is True
    assert "conflicts_truncated" not in out["data"]  # 空桶不标记


# ---------- 治理轮 I5：_cap_deferred 截 20 + 报 total ----------


def test_cap_deferred_truncates_and_reports_total():
    from rsi_boot.api.tools.knowledge_review_tool import _cap_deferred
    dd = [{"id": str(i), "possible_duplicates": []} for i in range(25)]
    out = _cap_deferred({"processed": 10, "new_status": "active", "deferred_duplicates": dd})
    assert out["deferred_total"] == 25
    assert len(out["deferred_duplicates"]) == 20
    # 20 条以内带 total 但不截
    out2 = _cap_deferred({"processed": 1, "deferred_duplicates": dd[:20]})
    assert out2["deferred_total"] == 20
    assert len(out2["deferred_duplicates"]) == 20
    # 无 deferred 保持原状
    assert _cap_deferred({"processed": 1}) == {"processed": 1}


# ---------- 治理轮 I4：批量晋升走预加载池，语义与逐条自检等价 ----------


async def test_batch_promotion_pool_blocks_and_reports(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    # 先落一条 active 正式记忆
    official = _write_official(
        store, title="命名约定", content="本项目统一使用 snake_case 命名变量和函数",
        type="convention", domain="docs",
    )
    # 再放一条近重复的 pending → 批量审批时应被暂缓
    dup_id = uuid.uuid4().hex
    from rsi_boot.memory.paths import pending_dir
    dest = pending_dir(store.rsi_dir, "convention") / memory_filename("命名约定 副本", dup_id)
    store.write(
        MemoryDoc(
            id=dup_id, type="convention", title="命名约定 副本",
            content="本项目统一使用 snake_case 命名变量和函数",
            domain="docs",
        ),
        dest=dest,
    )
    # 再来一条完全不像的 pending，应正常晋升
    ok_id = uuid.uuid4().hex
    dest_ok = pending_dir(store.rsi_dir, "convention") / memory_filename("无关约定", ok_id)
    store.write(
        MemoryDoc(
            id=ok_id, type="convention", title="无关约定",
            content="列表接口统一 page 从 1 开始",
            domain="docs",
        ),
        dest=dest_ok,
    )
    del official  # 引用一下防 lint
    result = await svc._review_batch_store(
        True, content_type="convention",
        block_duplicates=True, dedup_threshold=0.6,
    )
    # 至少 1 条晋升 + 1 条被暂缓（池化后语义等价逐条 find_promotion_duplicates）
    assert result["processed"] >= 1
    dd = result.get("deferred_duplicates") or []
    assert any(row["id"] == dup_id for row in dd)
