"""反馈提取：过门槛的 rejected/modified → pending 规范 YAML + candidates.jsonl。

只写 memory/pending/prohibitions|conventions 与 logs/candidates.jsonl。
不写 gene-map/、teaching-cases/、patterns/ 或正式规范目录。
规则式提取与 KnowledgeExtractor.extract_draft_rule 同源（v3.0 §4.4）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rsi_boot.memory.paths import pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc

_THRESHOLD_ACTIONS = frozenset({"rejected", "modified"})
_PENDING_TYPES = frozenset({"prohibition", "convention"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize_title(text: str, limit: int) -> str:
    """取首行/首句作为标题摘要，超长截断。"""
    summary = text.strip().splitlines()[0] if text.strip() else ""
    return summary[:limit]


def extract_draft_rule(
    candidate_type: str, question: str, answer: str,
) -> Optional[Dict[str, Any]]:
    """三类候选的规则式草稿生成；无提取价值（内容为空）返回 None。"""
    answer = (answer or "").strip()
    if not answer:
        return None
    if candidate_type == "rejected":
        title = summarize_title(answer, 27)
        return {
            "title": f"禁止：{title}" if not title.startswith("禁止") else title[:30],
            "content": answer[:2000],
            "content_type": "prohibition",
            "tags": [], "domain": None, "roles": [],
        }
    if candidate_type == "modified":
        return {
            "title": f"经验：{summarize_title(question, 24)}",
            "content": f"任务背景：{question[:200]}\n\n验证有效的做法：\n{answer[:1800]}",
            "content_type": "convention",
            "tags": [], "domain": None, "roles": [],
        }
    return {
        "title": summarize_title(question, 30),
        "content": f"Q: {question[:200]}\nA: {answer[:1800]}",
        "content_type": "convention",
        "tags": [], "domain": None, "roles": [],
    }


def _append_candidate(rsi_dir: Path, row: Dict[str, Any]) -> None:
    path = rsi_dir / "logs" / "candidates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()


def extract_from_feedback(
    rsi_dir: Path,
    *,
    action: str,
    comment: str | None = None,
    retrieved: List[str] | None = None,
    modified_content: str | None = None,
    question: str = "",
) -> List[Path]:
    """过门槛的 rejected/modified 写入 pending 规范，并追加 candidates.jsonl。

    返回相对 `.rsi` 的 Path（posix 以 memory/pending/prohibitions|conventions 开头）。
    """
    if action not in _THRESHOLD_ACTIONS:
        return []

    rsi_dir = Path(rsi_dir)
    if action == "rejected":
        candidate_type = "rejected"
        answer = comment or ""
        q = question
    else:
        candidate_type = "modified"
        answer = modified_content or comment or ""
        q = question or (comment or "")

    draft = extract_draft_rule(candidate_type, q, answer)
    if draft is None:
        return []

    typ = draft["content_type"]
    if typ not in _PENDING_TYPES:
        return []

    store = MemoryStore(rsi_dir)
    item_id = uuid.uuid4().hex
    refs = [item for item in (retrieved or []) if isinstance(item, str)]
    doc = MemoryDoc(
        id=item_id,
        type=typ,
        title=str(draft["title"])[:120],
        content=str(draft["content"])[:20000],
        tags=list(draft.get("tags") or []),
        roles=list(draft.get("roles") or []),
        domain=draft.get("domain"),
        source="auto-extract",
        refs=refs,
        extra={"candidate_type": candidate_type},
    )
    dest = pending_dir(store.rsi_dir, typ) / memory_filename(doc.title, item_id)
    written = store.write(doc, dest=dest)
    rel = Path((written.path or "").replace("\\", "/"))

    _append_candidate(rsi_dir, {
        "ts": _utc_now(),
        "kind": "candidate",
        "action": action,
        "candidate_type": candidate_type,
        "id": written.id,
        "path": written.path,
        "retrieved": refs,
        "status": "extracted",
    })
    return [rel]
