"""rsi_knowledge_search 工具（§7.1）：搜索知识库（query 必填，top_k ≤ 20）。"""

from __future__ import annotations

import logging
from typing import Any

from ...rag.query import expand_query
from ...services.knowledge_service import KnowledgeService
from ...ux.lang import locale_lang
from ...ux.messages import TOOL_DESC, t

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_search"
TOOL_DESCRIPTION = TOOL_DESC["knowledge_search"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索查询"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "role": {"type": "string", "description": "角色过滤"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限"},
    },
    "required": ["query"],
}


async def handle(knowledge: KnowledgeService, arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return {"status": "error", "message": t("QUERY_REQUIRED", locale_lang())}
    top_k = min(20, max(1, int(arguments.get("top_k") or 5)))
    role = arguments.get("role") or None
    expanded, _, _ = expand_query(query, role=role)
    items = await knowledge.search(
        expanded,
        str(arguments.get("project_id") or ""),
        role=role,
        top_k=top_k,
    )
    return {
        "status": "ok",
        "items": [
            {"id": it.id, "title": it.title, "content": it.content, "tags": it.tags, "domain": it.domain}
            for it in items
        ],
    }
