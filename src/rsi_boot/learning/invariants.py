"""不变量自检（借鉴 OpenMausBot server/runtime/invariants.ts 的 I1–I10 + replayStatus）。

理念：events.jsonl / candidates.jsonl 是 append-only 事实源，memory 库状态应能
从事件回放对账。本模块纯读：不修改、不清洗任何数据，只报告违例。

V1 提案证据链：auto-extract 条目须有 candidates.jsonl 行或 review 事件；
   promote 条目须有 distilled_from 图边。
V2 状态互斥：同一 doc id 不同时处于 pending_review 与 active；任何 id 不重复落盘。
   例外：teaching_case + gene_case 同 id 是 teach_record 的成对设计（一条教训两个视图）。
V3 反馈归因：近 30 天（reject 清理窗口）feedback 事件的 retrieved id 全部存在。
V4 审批回放：按 id 回放 review 事件，最终动作与磁盘状态一致
   （approve 后不得仍是 pending_review；reject 后不得是 active）。
V5 审批链：active 且 source ∈ {auto-extract, promote} 的条目须有 review approve 事件。
V6 低置信自洽：low_confidence=true 必有 count ≥ 1 与标记时间。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..memory.graph import load_catalog
from ..memory.logstore import iter_events
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

# V3 只查最近窗口：reject 条目留存 30 天后清理（knowledge_review_tool 约定），
# 更早的反馈引用已清理条目属正常衰减，不算归因断裂。
ATTRIBUTION_WINDOW_DAYS = 30

_GOVERNED_SOURCES = frozenset({"auto-extract", "promote"})
_EXAMPLE_CAP = 5
# teach_record 对一条教训写 teaching_case + gene_case 两个视图，共用一个 id（成对设计）
_DESIGNED_PAIR = frozenset({"teaching_case", "gene_case"})


@dataclass
class InvariantReport:
    code: str
    name: str
    problems: List[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_time(event: dict) -> datetime | None:
    text = str(event.get("ts") or event.get("created_at") or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cap(problems: List[str]) -> List[str]:
    """问题清单截断：最多 _EXAMPLE_CAP 条示例 + 余量计数。"""
    if len(problems) <= _EXAMPLE_CAP:
        return problems
    return problems[:_EXAMPLE_CAP] + [f"……另有 {len(problems) - _EXAMPLE_CAP} 条"]


def _candidate_ids(rsi_dir: Path) -> set[str]:
    """candidates.jsonl 是 append-only 事实源；坏行跳过（logstore 另有 errors.jsonl 记录）。"""
    path = rsi_dir / "logs" / "candidates.jsonl"
    ids: set[str] = set()
    if not path.is_file():
        return ids
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ids
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("id"), str):
            ids.add(obj["id"])
    return ids


def replay_review_actions(events: List[dict]) -> Dict[str, str]:
    """按 id 回放 review 事件流，后写覆盖先写（replayStatus 的 rsi-boot 对应物）。

    返回 {doc_id: 最终动作}；skip 不表达状态迁移，回放时忽略。
    """
    final: Dict[str, str] = {}
    for event in events:
        action = str(event.get("action") or "")
        if action not in ("approve", "reject"):
            continue
        ids = event.get("ids")
        if not isinstance(ids, list):
            continue
        for doc_id in ids:
            if isinstance(doc_id, str) and doc_id:
                final[doc_id] = action
    return final


def check_invariants(rsi_dir: Path) -> List[InvariantReport]:
    """对 .rsi/ 目录跑全部不变量，返回逐条报告。纯读；任何单条异常不外溢。"""
    rsi_dir = Path(rsi_dir)
    store = MemoryStore(rsi_dir)
    docs = store.list_all()
    by_id: Dict[str, list] = {}
    for doc in docs:
        by_id.setdefault(doc.id, []).append(doc)
    all_ids = set(by_id)

    review_events = list(iter_events(rsi_dir, kinds={"review"}, all_rolls=True))
    reviewed_ids: set[str] = set()
    for event in review_events:
        ids = event.get("ids")
        if isinstance(ids, list):
            reviewed_ids.update(str(i) for i in ids if isinstance(i, str))
    approved_ids = {
        doc_id for doc_id, action in replay_review_actions(review_events).items()
        if action == "approve"
    }

    reports = [
        _v1_evidence_chain(rsi_dir, docs, reviewed_ids),
        _v2_status_exclusive(by_id),
        _v3_feedback_attribution(rsi_dir, all_ids=all_ids),
        _v4_review_replay(review_events, by_id),
        _v5_approval_chain(docs, approved_ids),
        _v6_low_confidence(docs),
    ]
    return reports


def _v1_evidence_chain(rsi_dir: Path, docs: list, reviewed_ids: set[str]) -> InvariantReport:
    report = InvariantReport("V1", "提案证据链完整")
    candidate_ids = _candidate_ids(rsi_dir)
    distilled_from = {
        str(e.get("from"))
        for e in load_catalog(rsi_dir).get("edges") or []
        if isinstance(e, dict) and e.get("rel") == "distilled_from" and e.get("from")
    }
    problems: List[str] = []
    for doc in docs:
        if doc.source == "auto-extract":
            report.checked += 1
            if doc.id not in candidate_ids and doc.id not in reviewed_ids:
                problems.append(f"V1 {doc.id}: auto-extract 条目无 candidates 行也无 review 事件")
        elif doc.source == "promote":
            report.checked += 1
            if doc.id not in distilled_from:
                problems.append(f"V1 {doc.id}: promote 条目无 distilled_from 图边")
    report.problems = _cap(problems)
    return report


def _v2_status_exclusive(by_id: Dict[str, list]) -> InvariantReport:
    report = InvariantReport("V2", "状态互斥与唯一落盘")
    problems: List[str] = []
    for doc_id, copies in by_id.items():
        report.checked += 1
        if len(copies) < 2:
            continue
        statuses = {doc.status for doc in copies}
        types = {doc.type for doc in copies}
        paths = "、".join(str(doc.path) for doc in copies)
        if "pending_review" in statuses and "active" in statuses:
            problems.append(f"V2 {doc_id}: 同时在 pending 与正式库（{paths}）")
        elif statuses == {"active"} and types == _DESIGNED_PAIR and len(copies) == 2:
            continue  # teach_record 成对设计：一条教训的 teaching/gene 双视图
        else:
            problems.append(f"V2 {doc_id}: 重复落盘 {len(copies)} 份（{paths}）")
    report.problems = _cap(problems)
    return report


def _v3_feedback_attribution(rsi_dir: Path, *, all_ids: set[str]) -> InvariantReport:
    report = InvariantReport("V3", "反馈归因完整")
    cutoff = _utc_now() - timedelta(days=ATTRIBUTION_WINDOW_DAYS)
    problems: List[str] = []
    for event in iter_events(rsi_dir, kinds={"feedback"}, all_rolls=True):
        ts = _event_time(event)
        if ts is not None and ts < cutoff:
            continue
        retrieved = event.get("retrieved")
        if not isinstance(retrieved, list):
            continue
        for doc_id in retrieved:
            report.checked += 1
            if isinstance(doc_id, str) and doc_id not in all_ids:
                problems.append(f"V3 {doc_id}: 反馈事件引用的条目已不存在")
    report.problems = _cap(problems)
    return report


def _v4_review_replay(events: List[dict], by_id: Dict[str, list]) -> InvariantReport:
    report = InvariantReport("V4", "审批回放与磁盘一致")
    final = replay_review_actions(events)
    problems: List[str] = []
    for doc_id, action in final.items():
        copies = by_id.get(doc_id)
        if not copies:
            continue  # 已清理（reject 留存期满删除）属正常
        report.checked += 1
        status = copies[0].status
        if action == "approve" and status == "pending_review":
            problems.append(f"V4 {doc_id}: 回放终态 approve，磁盘仍是 pending_review")
        elif action == "reject" and status == "active":
            problems.append(f"V4 {doc_id}: 回放终态 reject，磁盘却是 active")
    report.problems = _cap(problems)
    return report


def _v5_approval_chain(docs: list, approved_ids: set[str]) -> InvariantReport:
    report = InvariantReport("V5", "受管来源的审批链完整")
    problems: List[str] = []
    for doc in docs:
        if doc.status != "active" or doc.source not in _GOVERNED_SOURCES:
            continue
        report.checked += 1
        if doc.id not in approved_ids:
            problems.append(f"V5 {doc.id}: active 的 {doc.source} 条目无 approve 事件痕迹")
    report.problems = _cap(problems)
    return report


def _v6_low_confidence(docs: list) -> InvariantReport:
    report = InvariantReport("V6", "低置信标记自洽")
    problems: List[str] = []
    for doc in docs:
        extra = doc.extra or {}
        if not extra.get("low_confidence"):
            continue
        report.checked += 1
        try:
            count = int(extra.get("low_confidence_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count < 1:
            problems.append(f"V6 {doc.id}: low_confidence=true 但计数缺失或 < 1")
        if not extra.get("low_confidence_at"):
            problems.append(f"V6 {doc.id}: low_confidence=true 但无标记时间")
    report.problems = _cap(problems)
    return report
