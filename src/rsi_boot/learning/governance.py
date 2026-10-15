"""记忆治理旁路（对标 Qoder「反思」）：把负反馈回流成条目级降置信标记。

零 Key、非阻断：某条记忆被召回注入后收到 rejected / 低分反馈，就在其 extra 上打
low_confidence 标记并累计次数；召回回传该标记，宿主据此「存疑」而非无条件遵守。
本模块只碰正式库里的 active 条目，采集物（harvest）跳过；任何单条异常都不外溢。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, List

from ..memory.harvest import is_harvest_doc
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

_REASON_MAX = 200


def mark_low_confidence(
    store: MemoryStore, doc_ids: Iterable[str], reason: str = "",
) -> int:
    """给被否反馈命中的正式条目打 low_confidence；返回实际标记条数。fail-open 单条跳过。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marked: List[str] = []
    for raw in doc_ids:
        doc_id = str(raw or "").strip().lower()
        if not doc_id:
            continue
        try:
            doc = store.read(doc_id)
        except (FileNotFoundError, ValueError):
            continue
        if doc.status != "active" or is_harvest_doc(doc):
            continue
        extra = dict(doc.extra or {})
        try:
            count = int(extra.get("low_confidence_count") or 0) + 1
        except (TypeError, ValueError):
            count = 1
        extra["low_confidence"] = True
        extra["low_confidence_count"] = count
        extra["low_confidence_at"] = now
        if reason:
            extra["low_confidence_reason"] = str(reason)[:_REASON_MAX]
        dest = store.rsi_dir / doc.path if doc.path else None
        if dest is None:
            continue
        try:
            store.write(doc.model_copy(update={"extra": extra}), dest=dest)
            marked.append(doc.id)
        except OSError:
            logger.warning("low_confidence 写回失败 id=%s", doc.id, exc_info=True)
    return len(marked)


def is_low_confidence(doc) -> bool:
    """读取侧判定：仅认布尔真值，兼容历史脏数据。"""
    return bool((getattr(doc, "extra", None) or {}).get("low_confidence"))
