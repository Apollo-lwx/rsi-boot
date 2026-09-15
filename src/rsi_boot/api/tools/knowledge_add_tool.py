"""rsi_knowledge_add 工具（§7.1）：添加知识条目（title + content 必填）。"""

from __future__ import annotations

import logging
from typing import Any

from ...core.models import KnowledgeItem
from ...services.knowledge_service import KnowledgeService
from ...ux.messages import TOOL_DESC

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_add"
TOOL_DESCRIPTION = TOOL_DESC["knowledge_add"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "条目标题"},
        "content": {"type": "string", "description": "条目正文"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
        "roles": {"type": "array", "items": {"type": "string"}, "description": "适用角色，空=通用"},
        "domain": {"type": "string", "description": "领域"},
        "content_type": {
            "type": "string",
            "description": "prohibition/convention/skill/documentation 或案例类型",
        },
    },
    "required": ["title", "content"],
}


async def handle(knowledge: KnowledgeService, arguments: dict[str, Any]) -> dict[str, Any]:
    title = str(arguments.get("title", "")).strip()
    content = str(arguments.get("content", "")).strip()
    if not title or not content:
        return {"status": "error", "message": "title 与 content 必填"}
    item = KnowledgeItem(
        project_id=str(arguments.get("project_id") or ""),
        title=title,
        content=content,
        content_type=str(arguments.get("content_type") or arguments.get("type") or "documentation"),
        tags=[str(t) for t in arguments.get("tags") or []],
        roles=[str(r) for r in arguments.get("roles") or []],
        domain=arguments.get("domain") or None,
    )
    result = await knowledge.add(item)
    if isinstance(result, dict) and result.get("status") == "error":
        return result
    if isinstance(result, dict):
        return {"status": "success", **result}
    return {"status": "success", "id": result}
