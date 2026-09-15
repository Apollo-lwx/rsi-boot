"""Identify leftover bootstrap harvest docs (not host-distilled knowledge)."""

from __future__ import annotations

HARVEST_TITLES = frozenset({
    "代码骨架摘要", "项目配置与规范摘要", "Git 历史分析", "跨信号关联图谱",
})
HARVEST_TAGS = frozenset({
    "signal:docs", "signal:doc-index", "signal:code",
    "signal:config", "signal:git", "signal:correlation",
})


def is_harvest_doc(doc) -> bool:
    tags = set(doc.tags or [])
    if "signal:distilled" in tags:
        return False
    if (doc.title or "") in HARVEST_TITLES:
        return True
    return bool(tags & HARVEST_TAGS)
