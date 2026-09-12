"""rsi_knowledge_review 工具（§4.3 人工确认）：审批知识提取草稿。

approve → active 并生成 embedding 进入检索；reject → rejected 留存 30 天后清理。
支持三种目标形态：单个 id、ids 数组批量、all_pending 全量（可按 content_type 过滤，
include_archived 含 bootstrap 限量溢出条目）。
"""

from __future__ import annotations

import logging
from typing import Any

from ...services.decision_queue import close_extract_runs
from ...services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_review"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "待审知识条目 ID（status=pending_review/archived）"},
        "ids": {"type": "array", "items": {"type": "string"}, "description": "批量审批的条目 ID 列表"},
        "all_pending": {"type": "boolean", "description": "true = 审批全部 pending_review 条目"},
        "content_type": {"type": "string", "description": "批量审批时按内容类型过滤（convention/architecture/faq/documentation）"},
        "include_archived": {"type": "boolean", "description": "批量审批时包含 archived（bootstrap 限量溢出）条目"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "action": {"type": "string", "enum": ["approve", "reject"], "description": "审批动作"},
    },
    "required": ["action"],
}


async def _tags_of(knowledge: KnowledgeService, item_ids: list[str]) -> list[Any]:
    if not item_ids:
        return []
    conn = await knowledge._db.connect()
    placeholders = ",".join("?" for _ in item_ids)
    async with conn.execute(
        f"SELECT tags FROM knowledge_items WHERE id IN ({placeholders})", item_ids,
    ) as cur:
        return [row["tags"] for row in await cur.fetchall()]


async def handle(runtime_or_knowledge: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    knowledge = getattr(runtime_or_knowledge, "knowledge", runtime_or_knowledge)
    decisions = getattr(runtime_or_knowledge, "decisions", None)
    project_id = str(arguments.get("project_id") or "")
    action = str(arguments.get("action", ""))
    if action not in ("approve", "reject"):
        return {"status": "error", "message": "action 非法（approve/reject）"}
    approve = action == "approve"

    ids = arguments.get("ids")
    if ids:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return {"status": "error", "message": "ids 必须是字符串数组"}
        tags = await _tags_of(knowledge, ids)
        result = await knowledge.review_batch(project_id, approve, ids=ids)
        if result.get("processed"):
            close_extract_runs(decisions, tags)
        return {"status": "ok", **result}

    if arguments.get("all_pending"):
        result = await knowledge.review_batch(
            project_id, approve,
            content_type=arguments.get("content_type") or None,
            include_archived=bool(arguments.get("include_archived")),
            exclude_bootstrap=True,
        )
        return {"status": "ok", **result}

    item_id = str(arguments.get("id", ""))
    if not item_id:
        return {"status": "error", "message": "缺少审批目标：id / ids / all_pending 三选一"}
    tags = await _tags_of(knowledge, [item_id])
    new_status = await knowledge.review(item_id, project_id, approve=approve)
    if new_status is None:
        return {"status": "error", "message": f"条目不存在或不处于待审状态: {item_id}"}
    close_extract_runs(decisions, tags)
    return {"status": "ok", "id": item_id, "new_status": new_status}
