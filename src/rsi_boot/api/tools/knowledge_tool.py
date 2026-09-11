"""rsi_knowledge_delete 工具（§8.3 删除权）：删除指定知识条目。"""

from __future__ import annotations

import logging
from typing import Any

from ...services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_delete"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "知识条目 ID"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
    },
    "required": ["id"],
}


async def handle(knowledge: KnowledgeService, arguments: dict[str, Any]) -> dict[str, Any]:
    item_id = str(arguments.get("id", ""))
    project_id = knowledge._scope(arguments.get("project_id"))
    if not item_id:
        return {"status": "error", "message": "缺少 id"}
    deleted = await knowledge.delete(item_id, project_id)
    if not deleted:
        return {"status": "error", "message": f"条目不存在或不属于项目 {project_id}: {item_id}"}
    return {"status": "ok", "deleted": item_id}
