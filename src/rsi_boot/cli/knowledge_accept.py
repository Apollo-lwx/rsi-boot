"""rsi knowledge accept：只放行本轮 bootstrap 抽取（车道 B），不碰 auto-extract。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from ..project import project_rsi_dir

logger = logging.getLogger(__name__)

_EXTRACT_SIGNALS = frozenset({
    "signal:conversation", "signal:rules", "signal:distilled",
})
_CONFLICT_TYPES = frozenset({"version", "doc_code", "incoherent"})


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def load_latest_run_id(
    project_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_rsi_dir(project_root) / "bootstrap_run.json")
    if db_path is not None:
        candidates.append(Path(db_path).parent / "bootstrap_run.json")
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        latest = data.get("latest") if isinstance(data, dict) else None
        if latest:
            return str(latest)
    return None


def _doc_source_url(doc: Any) -> str:
    extra = getattr(doc, "extra", None) or {}
    return str(extra.get("source_url") or getattr(doc, "source", None) or "")


def iter_run_extracts(store: Any, run_id: str) -> list[Any]:
    """本轮 bootstrap 抽取：pending_review + conversation|rules，含 documentation/faq。"""
    marker = f"bootstrap_run_id:{run_id}"
    out: list[Any] = []
    for doc in store.list_pending():
        if getattr(doc, "status", None) != "pending_review":
            continue
        if _doc_source_url(doc) == "auto-extract":
            continue
        tags = list(doc.tags or [])
        if marker not in tags:
            continue
        if not _EXTRACT_SIGNALS.intersection(tags):
            continue
        out.append(doc)
    return out


async def _extract_ids(runtime: Any, run_id: str) -> list[str]:
    store = getattr(runtime, "store", None)
    if store is not None:
        return [doc.id for doc in iter_run_extracts(store, run_id)]
    marker = f"bootstrap_run_id:{run_id}"
    ids: list[str] = []
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id, tags, source_url FROM knowledge_items "
        "WHERE project_id = ? AND status = 'pending_review'",
        (runtime.project_id or "",),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        if (row["source_url"] or "") == "auto-extract":
            continue
        tags = _parse_tags(row["tags"])
        if marker not in tags:
            continue
        if not _EXTRACT_SIGNALS.intersection(tags):
            continue
        ids.append(row["id"])
    return ids


async def _conflicts_for_run(runtime: Any, run_id: str) -> list[dict[str, Any]]:
    listed = await runtime.conflict_detector.list_conflicts(runtime.project_id or "", "open")
    marker = f"bootstrap_run_id:{run_id}"
    store = getattr(runtime, "store", None)
    out: list[dict[str, Any]] = []
    if store is not None:
        docs = list(store.list_all())
        by_id = {doc.id: doc for doc in docs}
        by_src = {_doc_source_url(doc): doc for doc in docs if _doc_source_url(doc)}
        for conflict in listed:
            ctype = conflict.get("conflict_type") or conflict.get("type")
            if ctype not in _CONFLICT_TYPES:
                continue
            item_id = conflict.get("item_id")
            doc = by_id.get(item_id) if item_id else None
            if doc is not None and marker in list(doc.tags or []):
                out.append(conflict)
                continue
            peer = conflict.get("user_rule_path")
            if not peer:
                continue
            prow = by_src.get(peer)
            if prow and marker in list(prow.tags or []):
                out.append(conflict)
        return out
    conn = await runtime.db.connect()
    for conflict in listed:
        if conflict.get("conflict_type") not in _CONFLICT_TYPES:
            continue
        item_id = conflict.get("item_id")
        tags_raw = ""
        if item_id:
            async with conn.execute(
                "SELECT tags FROM knowledge_items WHERE id = ?", (item_id,),
            ) as cur:
                row = await cur.fetchone()
            tags_raw = row["tags"] if row else ""
        if marker in _parse_tags(tags_raw):
            out.append(conflict)
            continue
        peer = conflict.get("user_rule_path")
        if not peer:
            continue
        async with conn.execute(
            "SELECT tags FROM knowledge_items WHERE project_id = ? AND source_url = ?",
            (runtime.project_id, peer),
        ) as cur:
            prow = await cur.fetchone()
        if prow and marker in _parse_tags(prow["tags"]):
            out.append(conflict)
    return out


def _recommended_of(conflict: dict[str, Any]) -> Optional[str]:
    note = conflict.get("resolution_note") or ""
    if note.startswith("recommended:"):
        return note.split(":", 1)[1].strip().split()[0]
    return None


async def accept_bootstrap_extracts(
    runtime: Any, *, run_id: Optional[str], reject: bool, conflicts: Optional[str],
) -> dict[str, Any]:
    """conflicts: None | 'tend' | 'coexist'。只处理 tags 含 bootstrap_run_id 且 signal conversation|rules。"""
    store = getattr(runtime, "store", None)
    db_hint = None
    if store is not None:
        db_hint = store.rsi_dir / "rsi.db"
    else:
        db_hint = getattr(getattr(runtime, "db", None), "db_path", None)
    rid = run_id or load_latest_run_id(
        getattr(runtime, "_project_root", None),
        db_hint,
    )
    processed = 0
    new_status = "rejected" if reject else "active"
    if rid:
        ids = await _extract_ids(runtime, rid)
        if ids:
            result = await runtime.knowledge.review_batch(
                runtime.project_id or "",
                approve=not reject,
                ids=ids,
                bootstrap_run_id=rid,
            )
            processed = int(result["processed"])
            new_status = str(result["new_status"])
            decisions = getattr(runtime, "decisions", None)
            if decisions is not None and processed:
                decisions.close(f"extract:{rid}")
    conflicts_resolved = 0
    if rid and conflicts in ("tend", "coexist"):
        for conflict in await _conflicts_for_run(runtime, rid):
            resolution = "coexist" if conflicts == "coexist" else _recommended_of(conflict)
            if not resolution:
                continue
            done = await runtime.conflict_detector.resolve(conflict["id"], resolution)
            if done:
                conflicts_resolved += 1
    return {
        "processed": processed,
        "new_status": new_status,
        "conflicts_resolved": conflicts_resolved,
        "run_id": rid,
    }


async def run_knowledge_accept(
    runtime: Any, *, run_id: Optional[str], reject: bool, conflicts: Optional[str],
) -> int:
    result = await accept_bootstrap_extracts(
        runtime, run_id=run_id, reject=reject, conflicts=conflicts,
    )
    if result["processed"] == 0:
        print("本轮无抽取项")
    else:
        verb = "拒绝" if reject else "放行"
        print(f"已{verb}本轮抽取 {result['processed']} 条")
    if result.get("conflicts_resolved"):
        print(f"已裁决冲突 {result['conflicts_resolved']} 组")
    return 0
