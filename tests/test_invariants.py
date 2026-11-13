"""不变量自检测试（任务 0.1）：每个不变量至少一个变异用例——破坏即红。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsi_boot.cli.doctor_command import run_doctor
from rsi_boot.learning.invariants import (
    ATTRIBUTION_WINDOW_DAYS,
    check_invariants,
    replay_review_actions,
)
from rsi_boot.memory.graph import add_edge
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc


def _ts(days_ago: float = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _doc(doc_id: str, **over) -> MemoryDoc:
    base = dict(
        id=doc_id, type="convention", title=f"条目 {doc_id[:6]}",
        content="内容", source="manual",
    )
    base.update(over)
    return MemoryDoc(**base)


def _write_official(store: MemoryStore, doc: MemoryDoc) -> MemoryDoc:
    dest = official_dir(store.rsi_dir, doc.type) / memory_filename(doc.title, doc.id)
    return store.write(doc, dest=dest)


def _write_pending(store: MemoryStore, doc: MemoryDoc) -> MemoryDoc:
    dest = pending_dir(store.rsi_dir, doc.type) / memory_filename(doc.title, doc.id)
    return store.write(doc, dest=dest)


def _append_candidate(rsi_dir: Path, doc_id: str) -> None:
    path = rsi_dir / "logs" / "candidates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": _ts(), "kind": "candidate", "id": doc_id, "status": "extracted"}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _review(rsi_dir: Path, action: str, ids: list[str]) -> None:
    append_event(rsi_dir, {
        "id": "e" * 32, "ts": _ts(), "kind": "review",
        "action": action, "ids": ids, "processed": len(ids),
    })


def _report(reports, code: str):
    return next(r for r in reports if r.code == code)


ID_A = "a" * 32
ID_B = "b" * 32
ID_C = "c" * 32
ID_SRC = "d" * 32
ID_MISSING = "e" * 32


# ---------- replay_review_actions 纯函数 ----------

def test_replay_last_action_wins():
    events = [
        {"action": "approve", "ids": [ID_A]},
        {"action": "reject", "ids": [ID_A]},
        {"action": "skip", "ids": [ID_B]},
        {"action": "approve", "ids": [ID_B, ID_C]},
    ]
    assert replay_review_actions(events) == {ID_A: "reject", ID_B: "approve", ID_C: "approve"}


def test_replay_ignores_malformed_rows():
    events = [{"action": "approve"}, {"action": "approve", "ids": "not-a-list"}, {"ids": [ID_A]}]
    assert replay_review_actions(events) == {}


# ---------- 全绿基线 ----------

def test_clean_tree_all_green(store):
    doc = _write_official(store, _doc(ID_A, source="auto-extract"))
    _append_candidate(store.rsi_dir, doc.id)
    _review(store.rsi_dir, "approve", [doc.id])
    promoted = _write_pending(store, _doc(ID_B, source="promote"))
    add_edge(store.rsi_dir, promoted.id, ID_SRC, "distilled_from")
    reports = check_invariants(store.rsi_dir)
    assert [r.code for r in reports] == ["V1", "V2", "V3", "V4", "V5", "V6"]
    assert all(r.ok for r in reports)


def test_empty_tree_is_green(tmp_path):
    reports = check_invariants(tmp_path / ".rsi")
    assert all(r.ok for r in reports)


# ---------- V1 提案证据链 ----------

def test_v1_flags_auto_extract_without_evidence(store):
    _write_official(store, _doc(ID_A, source="auto-extract"))
    report = _report(check_invariants(store.rsi_dir), "V1")
    assert not report.ok
    assert any(ID_A in p for p in report.problems)


def test_v1_promote_requires_edge(store):
    _write_pending(store, _doc(ID_B, source="promote"))
    assert not _report(check_invariants(store.rsi_dir), "V1").ok
    add_edge(store.rsi_dir, ID_B, ID_SRC, "distilled_from")
    assert _report(check_invariants(store.rsi_dir), "V1").ok


def test_v1_manual_source_exempt(store):
    _write_official(store, _doc(ID_A, source="manual"))
    assert _report(check_invariants(store.rsi_dir), "V1").ok


# ---------- V2 状态互斥 ----------

def test_v2_flags_pending_and_official_same_id(store):
    _write_pending(store, _doc(ID_A))
    _write_official(store, _doc(ID_A))
    report = _report(check_invariants(store.rsi_dir), "V2")
    assert not report.ok
    assert any("同时在 pending 与正式库" in p for p in report.problems)


def test_v2_flags_duplicate_official_copies(store):
    _write_official(store, _doc(ID_A, title="第一份"))
    _write_official(store, _doc(ID_A, title="第二份"))
    report = _report(check_invariants(store.rsi_dir), "V2")
    assert not report.ok
    assert any("重复落盘" in p for p in report.problems)


def test_v2_teaching_gene_pair_is_by_design(store):
    """teach_record 对同一 id 写 teaching_case + gene_case 两个视图，不算违例。"""
    base = official_dir(store.rsi_dir, "teaching_case")
    store.write(_doc(ID_A, type="teaching_case"), dest=base / memory_filename("教训", ID_A))
    gene_base = official_dir(store.rsi_dir, "gene_case")
    store.write(_doc(ID_A, type="gene_case"), dest=gene_base / memory_filename("教训", ID_A))
    assert _report(check_invariants(store.rsi_dir), "V2").ok


def test_v2_other_cross_type_duplicate_flagged(store):
    _write_official(store, _doc(ID_A, type="convention", title="规约"))
    _write_official(store, _doc(ID_A, type="documentation", title="文档"))
    assert not _report(check_invariants(store.rsi_dir), "V2").ok


# ---------- V3 反馈归因 ----------

def test_v3_flags_recent_feedback_to_missing_doc(store):
    append_event(store.rsi_dir, {
        "id": "f" * 32, "ts": _ts(), "kind": "feedback",
        "token": "tok", "action": "rejected", "retrieved": [ID_MISSING],
    })
    report = _report(check_invariants(store.rsi_dir), "V3")
    assert not report.ok
    assert any(ID_MISSING in p for p in report.problems)


def test_v3_ignores_feedback_beyond_window(store):
    old = _ts(ATTRIBUTION_WINDOW_DAYS + 10)
    append_event(store.rsi_dir, {
        "id": "f" * 32, "ts": old, "kind": "feedback",
        "token": "tok", "action": "rejected", "retrieved": [ID_MISSING],
    })
    assert _report(check_invariants(store.rsi_dir), "V3").ok


def test_v3_green_when_retrieved_exists(store):
    doc = _write_official(store, _doc(ID_A))
    append_event(store.rsi_dir, {
        "id": "f" * 32, "ts": _ts(), "kind": "feedback",
        "token": "tok", "action": "accepted", "retrieved": [doc.id],
    })
    assert _report(check_invariants(store.rsi_dir), "V3").ok


# ---------- V4 审批回放 ----------

def test_v4_flags_approved_but_still_pending(store):
    doc = _write_pending(store, _doc(ID_A))
    _review(store.rsi_dir, "approve", [doc.id])
    report = _report(check_invariants(store.rsi_dir), "V4")
    assert not report.ok
    assert any("pending_review" in p for p in report.problems)


def test_v4_flags_rejected_but_active(store):
    doc = _write_official(store, _doc(ID_A))
    _review(store.rsi_dir, "reject", [doc.id])
    report = _report(check_invariants(store.rsi_dir), "V4")
    assert not report.ok
    assert any("active" in p for p in report.problems)


def test_v4_missing_doc_is_normal_purge(store):
    _review(store.rsi_dir, "reject", [ID_MISSING])
    assert _report(check_invariants(store.rsi_dir), "V4").ok


# ---------- V5 审批链 ----------

def test_v5_flags_active_auto_extract_without_approve_event(store):
    doc = _write_official(store, _doc(ID_A, source="auto-extract"))
    _append_candidate(store.rsi_dir, doc.id)  # V1 通过，V5 单独红
    report = _report(check_invariants(store.rsi_dir), "V5")
    assert not report.ok
    assert any(ID_A in p for p in report.problems)


def test_v5_pending_not_required(store):
    _write_pending(store, _doc(ID_A, source="auto-extract"))
    assert _report(check_invariants(store.rsi_dir), "V5").ok


# ---------- V6 低置信自洽 ----------

def test_v6_flags_missing_count_and_time(store):
    _write_official(store, _doc(ID_A, extra={"low_confidence": True}))
    report = _report(check_invariants(store.rsi_dir), "V6")
    assert not report.ok
    assert len(report.problems) == 2


def test_v6_green_with_complete_marks(store):
    _write_official(store, _doc(ID_A, extra={
        "low_confidence": True, "low_confidence_count": 2,
        "low_confidence_at": _ts(),
    }))
    assert _report(check_invariants(store.rsi_dir), "V6").ok


# ---------- doctor --invariants CLI ----------

def test_doctor_invariants_exit_codes(store, capsys):
    ok_args = SimpleNamespace(project_root=str(store.rsi_dir.parent), fix=False, invariants=True)
    assert run_doctor(ok_args) == 0
    _write_pending(store, _doc(ID_A, source="auto-extract"))
    _review(store.rsi_dir, "approve", [ID_A])  # 制造 V4 违例
    assert run_doctor(ok_args) == 1
    out = capsys.readouterr().out
    assert "V4" in out and "存在违例" in out


def test_doctor_default_flow_untouched(store, capsys):
    """--invariants 不影响 doctor 原有 git 追踪检查路径。"""
    args = SimpleNamespace(project_root=str(store.rsi_dir.parent), fix=False, invariants=False)
    run_doctor(args)  # 非 git 仓库 → 报断层风险，返回 1
    out = capsys.readouterr().out
    assert "git" in out
