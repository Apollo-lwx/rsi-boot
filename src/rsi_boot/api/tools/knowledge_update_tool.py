"""rsi_knowledge_update 工具：原地更新已有记忆 + 合并近重复（对标 Qoder「update 优于新建」）。

action=update：按 id 改 title/content/tags/roles/domain/description/trigger_condition/
expiry_condition 中任意子集，保留 id 与所在目录，写回触发注入重写。
action=merge：合并近重复候选，保留更完整者、其余归档（可恢复）并写 supersedes 边。
"""

from __future__ import annotations

import logging
from typing import Any

from ...services.knowledge_service import KnowledgeService
from ...ux.messages import TOOL_DESC

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_update"
TOOL_DESCRIPTION = TOOL_DESC["knowledge_update"]

_EDITABLE = (
    "title", "content", "tags", "roles", "domain",
    "description", "trigger_condition", "expiry_condition",
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string", "enum": ["update", "merge"],
            "description": "update=按 id 原地改字段（默认）；merge=合并近重复",
        },
        "id": {"type": "string", "description": "update 必填：目标条目 id"},
        "title": {"type": "string", "description": "新标题（≤120 字）"},
        "content": {"type": "string", "description": "新正文（短知识≤1500，案例类≤20000）"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（整体替换）"},
        "roles": {"type": "array", "items": {"type": "string"}, "description": "适用角色（整体替换）"},
        "domain": {"type": "string", "description": "领域/板块"},
        "description": {"type": "string", "description": "一句话简述"},
        "trigger_condition": {"type": "string", "description": "前提条件"},
        "expiry_condition": {"type": "string", "description": "失效条件"},
        "ids": {
            "type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 20,
            "description": "merge 必填：待合并的条目 id（≥2）",
        },
        "keep_id": {
            "type": "string",
            "description": "merge 可选：指定保留者；缺省自动取正文最长/最新的一条",
        },
    },
}


async def handle(knowledge: KnowledgeService, arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action") or "update").strip().lower()
    if action == "merge":
        ids = [str(x) for x in (arguments.get("ids") or []) if str(x).strip()]
        keep_id = str(arguments.get("keep_id") or "").strip() or None
        return await knowledge.merge(ids, keep_id=keep_id)
    item_id = str(arguments.get("id") or "").strip().lower()
    if not item_id:
        return {"status": "error", "code": "invalid", "message": "update 需要 id"}
    patch = {k: arguments[k] for k in _EDITABLE if k in arguments}
    return await knowledge.update(item_id, patch)
