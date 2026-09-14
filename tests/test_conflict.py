"""用户规则冲突检测测试（Spec v3.1 §4.9，A7）：三类启发式（contradiction/stale/overlap）、
三选一裁决流转、memory_wins 自动关闭、rsi_conflicts 工具。"""

import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.injector.targets import AgentsMdTarget
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc

from memory_helpers import memory_conflict_rows


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / ".rsi")


def _detector(store: MemoryStore, tmp_path: Path, on_change=None) -> ConflictDetector:
    return ConflictDetector(store=store, project_root=tmp_path, on_change=on_change)


def _add_memory(
    store: MemoryStore,
    title="禁止：使用裸 SQL",
    content="必须参数化查询",
    type="prohibition",
    domain="database",
    tags=None,
) -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(
        MemoryDoc(
            id=item_id, type=type, title=title, content=content,
            domain=domain, tags=list(tags or []),
        ),
        dest=dest,
    )
    return item_id


def _write_user_rule(root: Path, name: str, text: str, mtime_days_ago: int = 0) -> Path:
    rules_dir = root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / name
    path.write_text(text, encoding="utf-8")
    if mtime_days_ago:
        old = time.time() - mtime_days_ago * 86400
        os.utime(path, (old, old))
    return path


def _seed_recall_hits(store: MemoryStore, doc_id: str, n: int = 3) -> None:
    for i in range(n):
        append_event(store.rsi_dir, {
            "id": f"e{i}-{uuid.uuid4().hex[:8]}",
            "kind": "recall",
            "retrieved": [doc_id],
            "task": "SQL",
        })


def _open_conflicts(tmp_path: Path) -> list[dict]:
    return [r for r in memory_conflict_rows(tmp_path) if r.get("status", "open") == "open"]


# ---------- 三类启发式 ----------


async def test_contradiction_prohibition_vs_permissive_rule(tmp_path):
    """学习禁止项 vs 用户规则许可式表述 → contradiction"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "数据库查询允许使用裸 SQL，方便快捷。")
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 1
    conflicts = _open_conflicts(tmp_path)
    assert conflicts[0]["conflict_type"] == "contradiction"
    assert conflicts[0]["user_rule_path"] == ".cursor/rules/my-rule.mdc"
    assert "使用裸 SQL" in conflicts[0]["user_rule_excerpt"]


async def test_contradiction_detected_in_claude_md(tmp_path):
    """CLAUDE.md 作为用户规则文件参与冲突检测（此前只扫 .cursor/rules 等）"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    (tmp_path / "CLAUDE.md").write_text("数据库查询允许使用裸 SQL，方便快捷。", encoding="utf-8")
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["scanned"] == 1
    assert stats["detected"] == 1
    conflicts = _open_conflicts(tmp_path)
    assert conflicts[0]["conflict_type"] == "contradiction"
    assert conflicts[0]["user_rule_path"] == "CLAUDE.md"


async def test_no_conflict_when_user_rule_also_prohibitive(tmp_path):
    """用户规则同为禁止式表述 = 与学习禁止项一致，不报冲突"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "禁止使用裸 SQL，一律参数化。")
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 0


async def test_overlap_convention_duplicated(tmp_path):
    """convention 记忆在用户规则中出现（非禁止语境）→ overlap（建议删手写副本）"""
    store = _store(tmp_path)
    _add_memory(store, title="提交信息规范", content="feat/fix 前缀",
                type="convention", domain=None)
    _write_user_rule(tmp_path, "git.mdc", "提交信息规范：使用 feat/fix 前缀。")
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 1
    assert _open_conflicts(tmp_path)[0]["conflict_type"] == "overlap"


async def test_stale_rule_with_active_adoption(tmp_path):
    """用户规则 90 天未更新 + 同主题记忆近 30 天采纳 ≥3 → stale"""
    store = _store(tmp_path)
    doc_id = _add_memory(store, title="禁止：使用裸 SQL", domain="database")
    _write_user_rule(tmp_path, "old.mdc", "禁止使用裸 SQL（旧规）。", mtime_days_ago=120)
    _seed_recall_hits(store, doc_id, n=3)
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 1
    assert _open_conflicts(tmp_path)[0]["conflict_type"] == "stale"


async def test_stale_requires_adoption_threshold(tmp_path):
    """陈旧文件但记忆近期零采纳 → 不报 stale（避免打扰）"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL", domain="database")
    _write_user_rule(tmp_path, "old.mdc", "禁止使用裸 SQL（旧规）。", mtime_days_ago=120)
    assert (await _detector(store, tmp_path).scan("p1"))["detected"] == 0


async def test_scan_scope_excludes_rsi_files_and_managed_block(tmp_path):
    """rsi-*.mdc（自产）与 AGENTS.md 托管块内内容不参与冲突判定"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "rsi-prohibition-abcd1234.mdc", "允许使用裸 SQL")  # 自产文件
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        f"{AgentsMdTarget.BEGIN}\n允许使用裸 SQL\n{AgentsMdTarget.END}\n", encoding="utf-8"
    )
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["scanned"] == 0 and stats["detected"] == 0


async def test_scan_idempotent_no_duplicates(tmp_path):
    """重复扫描不产生重复冲突行（conflicts.yaml 去重）"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    detector = _detector(store, tmp_path)
    await detector.scan("p1")
    stats = await detector.scan("p1")
    assert stats["detected"] == 0
    assert len(_open_conflicts(tmp_path)) == 1


# ---------- ISSUE-4：词边界 / 极性窗口 / overlap 优先级 ----------


async def test_word_boundary_no_substring_false_positive(tmp_path):
    """英文短语按词边界匹配：Executor 不命中 ThreadPoolExecutor 内部子串"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：Executor", content="禁止直接创建线程池")
    _write_user_rule(tmp_path, "threading.mdc", "请使用 ThreadPoolExecutor 管理线程池。")
    assert (await _detector(store, tmp_path).scan("p1"))["detected"] == 0


async def test_word_boundary_still_matches_exact(tmp_path):
    """词边界不误伤正常命中：独立出现的 Executor 仍触发检测"""
    store = _store(tmp_path)
    _add_memory(store, title="Executor", content="工厂方法", type="convention", domain=None)
    _write_user_rule(tmp_path, "threading.mdc", "请使用 Executor 工厂方法创建线程池。")
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 1
    assert _open_conflicts(tmp_path)[0]["conflict_type"] == "overlap"


async def test_contradiction_requires_permissive_window(tmp_path):
    """方向一致（而非/统一）的中性窗口不报 contradiction：许可词存在才报"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：ResponseEntity", content="统一响应包装")
    _write_user_rule(tmp_path, "resp.mdc", "统一 ApiDataResponse<T> 而非 ResponseEntity。")
    assert (await _detector(store, tmp_path).scan("p1"))["detected"] == 0


async def test_overlap_priority_over_stale(tmp_path):
    """convention 命中即 overlap——即使窗口含禁止词、文件陈旧，也不落入 stale"""
    store = _store(tmp_path)
    doc_id = _add_memory(store, title="ApiDataResponse", content="统一响应包装",
                         type="convention", domain="api")
    _write_user_rule(tmp_path, "resp.mdc", "禁止直接返回 ResponseEntity，统一 ApiDataResponse。",
                     mtime_days_ago=120)
    _seed_recall_hits(store, doc_id, n=3)
    stats = await _detector(store, tmp_path).scan("p1")
    assert stats["detected"] == 1
    assert _open_conflicts(tmp_path)[0]["conflict_type"] == "overlap"


# ---------- 裁决流转 ----------


async def test_resolve_user_wins_suppresses_memory(tmp_path):
    """user_wins：学习记忆归档（不再注入），冲突关闭"""
    store = _store(tmp_path)
    item_id = _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    on_change_calls = []

    async def on_change(project_id):
        on_change_calls.append(project_id)

    detector = _detector(store, tmp_path, on_change=on_change)
    await detector.scan("p1")
    conflict_id = _open_conflicts(tmp_path)[0]["id"]

    result = await detector.resolve(conflict_id, "user_wins", note="团队规约优先")
    assert result is not None and "不再注入" in result["guidance"]
    assert on_change_calls == ["p1"]  # 触发注入重写

    assert store.read(item_id).status == "archived"
    assert _open_conflicts(tmp_path) == []
    # 已裁决冲突不可二次裁决
    assert await detector.resolve(conflict_id, "coexist") is None


async def test_resolve_memory_wins_auto_close_on_file_change(tmp_path):
    """memory_wins：指引用户手动改文件；下次扫描检测到 hash 变更 → 自动关闭"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    rule_path = _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    detector = _detector(store, tmp_path)
    await detector.scan("p1")
    conflict_id = _open_conflicts(tmp_path)[0]["id"]

    result = await detector.resolve(conflict_id, "memory_wins")
    assert "my-rule.mdc" in result["guidance"]  # 返回用户文件路径待手动改

    # 用户手动修改规则文件 → 下次扫描自动关闭
    rule_path.write_text("禁止使用裸 SQL，一律参数化。", encoding="utf-8")
    stats = await detector.scan("p1")
    assert stats["closed"] == 1
    closed = await detector.list_conflicts("p1", status="closed")
    assert len(closed) == 1 and closed[0]["status"] == "closed"


async def test_resolve_memory_wins_auto_close_on_file_delete(tmp_path):
    """memory_wins 后用户直接删除规则文件 → 同样自动关闭"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    rule_path = _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    detector = _detector(store, tmp_path)
    await detector.scan("p1")
    conflict_id = _open_conflicts(tmp_path)[0]["id"]
    await detector.resolve(conflict_id, "memory_wins")

    rule_path.unlink()
    assert (await detector.scan("p1"))["closed"] == 1


async def test_resolve_coexist(tmp_path):
    """coexist：标记共存，该组合不再提醒"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    detector = _detector(store, tmp_path)
    await detector.scan("p1")
    conflict_id = _open_conflicts(tmp_path)[0]["id"]

    result = await detector.resolve(conflict_id, "coexist")
    assert "共存" in result["guidance"]
    items = await detector.list_conflicts("p1", status="coexist")
    assert len(items) == 1


# ---------- rsi_conflicts 工具 ----------


def _runtime(detector):
    return SimpleNamespace(conflict_detector=detector)


async def test_conflicts_tool_flow(tmp_path):
    """工具层：scan → list → resolve 全链路"""
    store = _store(tmp_path)
    _add_memory(store, title="禁止：使用裸 SQL")
    _write_user_rule(tmp_path, "my-rule.mdc", "允许使用裸 SQL。")
    runtime = _runtime(_detector(store, tmp_path))

    scan = await conflicts_tool.handle(runtime, {"action": "scan", "project_id": "p1"})
    assert scan["status"] == "success" and scan["data"]["detected"] == 1

    listed = await conflicts_tool.handle(runtime, {"action": "list", "project_id": "p1"})
    assert listed["data"]["count"] == 1
    conflict_id = listed["data"]["conflicts"][0]["id"]

    resolved = await conflicts_tool.handle(runtime, {
        "action": "resolve", "project_id": "p1",
        "conflict_id": conflict_id, "resolution": "coexist",
    })
    assert resolved["status"] == "success"

    # 非法 resolution / 未知 action 报错
    bad = await conflicts_tool.handle(runtime, {
        "action": "resolve", "conflict_id": conflict_id, "resolution": "delete_user_file",
    })
    assert bad["status"] == "error"
    unknown = await conflicts_tool.handle(runtime, {"action": "purge"})
    assert unknown["status"] == "error"
