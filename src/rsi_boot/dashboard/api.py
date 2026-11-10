"""大屏的详情/列表读接口与审批写接口。

server 只绑 127.0.0.1；审批复用 KnowledgeService 的既有搬家逻辑
（approve → official / reject → archive），并补写一条 review 事件
（与 rsi_knowledge_review 同口径，via=web_ui 标记来源）。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory.logstore import append_event
from ..memory.store import MemoryStore
from ..memory.types import MemoryDoc, status_from_path
from ..services.knowledge_service import KnowledgeService
from .snapshot import _category, _clip, _iter_event_logs

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_LIST_CAP = 200
_RECALL_CAP = 30
_EVENT_CAP = 50


def _doc_status(doc: MemoryDoc) -> str:
    return status_from_path(doc.path) if doc.path else doc.status


def _doc_brief(doc: MemoryDoc) -> dict[str, Any]:
    stamp = doc.updated_at or doc.created_at or ""
    return {
        "id": doc.id,
        "title": doc.title,
        "type": doc.type,
        "domain": doc.domain or "",
        "status": _doc_status(doc),
        "tags": list(doc.tags or []),
        "updated": stamp[:10],
        "time": stamp[11:16] if len(stamp) >= 16 else "",
        "snippet": _clip(doc.content or "", 80),
    }


def memory_detail(rsi_dir: Path, doc_id: str) -> dict[str, Any] | None:
    """单条记忆全文 + 出处元数据。id 非法或不存在返回 None。"""
    if not _ID_RE.match(doc_id):
        return None
    store = MemoryStore(rsi_dir)
    try:
        doc = store.read(doc_id)
    except Exception:  # 损坏 YAML / 校验失败都按不存在处理，不让大屏 500
        return None
    brief = _doc_brief(doc)
    brief.update({
        "content": doc.content or "",
        "source": doc.source or "",
        "description": doc.description or "",
        "trigger": doc.trigger_condition or "",
        "expiry": doc.expiry_condition or "",
        "roles": list(doc.roles or []),
        "created": doc.created_at or "",
        "updated_full": doc.updated_at or "",
        "path": doc.path or "",
        "category": _category(doc.path or ""),
    })
    return brief


def memory_list(
    rsi_dir: Path, *, category: str = "", status: str = "", domain: str = "",
) -> list[dict[str, Any]]:
    """按类型/状态/模块列记忆（列表页用，不含全文）。domain=unknown 匹配未标注。"""
    store = MemoryStore(rsi_dir)
    rows: list[dict[str, Any]] = []
    for doc in store.list_all():
        rel = doc.path or ""
        cat = _category(rel)
        stat = _doc_status(doc)
        if category and cat != category:
            continue
        if status and stat != status:
            continue
        if domain and (doc.domain or "unknown") != domain:
            continue
        row = _doc_brief(doc)
        row["category"] = cat
        rows.append(row)
    rows.sort(key=lambda row: row["updated"], reverse=True)
    return rows[:_LIST_CAP]


def event_list(rsi_dir: Path, kind: str = "") -> list[dict[str, Any]]:
    """近期事件流（反馈构成等下钻用），只带展示需要的字段。

    feedback 事件本身不记任务文本，用 token 反查同次 recall 的 task 补上证据链；
    附带 comment（反馈理由）。
    """
    events = list(_iter_event_logs(Path(rsi_dir)))
    token_task: dict[str, str] = {}
    for event in events:
        if event.get("kind") == "recall":
            token = event.get("token")
            if isinstance(token, str) and token:
                token_task.setdefault(token, str(event.get("task") or ""))
    out: list[dict[str, Any]] = []
    for event in events:
        if kind and event.get("kind") != kind:
            continue
        row = {
            key: event.get(key)
            for key in ("ts", "kind", "action", "rating", "task", "processed", "comment")
            if event.get(key) is not None
        }
        if event.get("kind") == "feedback" and not row.get("task"):
            task = token_task.get(str(event.get("token") or ""))
            if task:
                row["task"] = task
        out.append(row)
    out.sort(key=lambda row: str(row.get("ts") or ""), reverse=True)
    return out[:_EVENT_CAP]


def recall_events(rsi_dir: Path) -> list[dict[str, Any]]:
    """近期召回事件：任务 + 命中记忆清单（标题现解，已淘汰的标 missing）。"""
    store = MemoryStore(rsi_dir)
    titles: dict[str, tuple[str, str]] = {}
    for doc in store.list_all():
        titles[doc.id] = (doc.title, doc.type)
    events: list[dict[str, Any]] = []
    for event in _iter_event_logs(Path(rsi_dir)):
        if event.get("kind") != "recall":
            continue
        retrieved = event.get("retrieved")
        ids = [mid for mid in retrieved if isinstance(mid, str)] if isinstance(retrieved, list) else []
        events.append({
            "ts": str(event.get("ts") or ""),
            "task": str(event.get("task") or ""),
            "hits": [
                {
                    "id": mid,
                    "title": titles.get(mid, ("（已不在当前记忆库）", ""))[0],
                    "type": titles.get(mid, ("", ""))[1],
                    "missing": mid not in titles,
                }
                for mid in ids
            ],
        })
    events.sort(key=lambda event: event["ts"], reverse=True)
    return events[:_RECALL_CAP]


def review(rsi_dir: Path, ids: list[str], action: str) -> dict[str, Any]:
    """批量审批入口：approve → active（official 目录），reject → archive。

    复用 KnowledgeService.review_batch；返回 {processed, new_status[, deferred_duplicates]}。
    """
    approve = action == "approve"
    store = MemoryStore(rsi_dir)
    knowledge = KnowledgeService(store)
    result = asyncio.run(knowledge.review_batch("", approve, ids=list(ids)))
    append_event(
        Path(rsi_dir),
        {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "review",
            "action": action,
            "processed": int(result.get("processed") or 0),
            "ids": [str(item) for item in ids if item][:200],
            "bootstrap_run_id": "",
            "via": "web_ui",
        },
    )
    return result
