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
        "pack_id": {"type": "string", "description": "阅读包 id；蒸馏时必填，写入 pack_id: 标签"},
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
    tags = [str(t) for t in arguments.get("tags") or []]
    pack_id = str(arguments.get("pack_id") or "").strip()
    content_type = str(arguments.get("content_type") or arguments.get("type") or "").strip()
    store = getattr(knowledge, "_store", None)
    if pack_id:
        marker = f"pack_id:{pack_id}"
        if marker not in tags:
            tags.append(marker)
        if "signal:distilled" not in tags:
            tags.append("signal:distilled")
        if store is not None:
            from ...scanner.reading_packs import distill_type_for_pack, load_index, load_pack

            pack = load_pack(store.rsi_dir, pack_id)
            if pack is not None and content_type != "prohibition":
                content_type = distill_type_for_pack(pack)
            run_id = load_index(store.rsi_dir).get("bootstrap_run_id")
            if run_id and not any(str(tag).startswith("bootstrap_run_id:") for tag in tags):
                tags.append(f"bootstrap_run_id:{run_id}")
    if not content_type:
        content_type = "documentation"
    item = KnowledgeItem(
        project_id=str(arguments.get("project_id") or ""),
        title=title,
        content=content,
        content_type=content_type,
        tags=tags,
        roles=[str(r) for r in arguments.get("roles") or []],
        domain=arguments.get("domain") or None,
    )
    result = await knowledge.add(item)
    if isinstance(result, dict) and result.get("status") == "error":
        return result
    if isinstance(result, dict):
        return {"status": "success", **result}
    return {"status": "success", "id": result}
