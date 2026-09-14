"""rsi_knowledge_review 工具（§4.3 人工确认）：审批知识提取草稿。

approve → active 并生成 embedding 进入检索；reject → rejected 留存 30 天后清理。
支持三种目标形态：单个 id、ids 数组批量、all_pending（默认不含 bootstrap 抽取，
可按 content_type 过滤，include_archived 含带 bootstrap_run_id 的溢出归档）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...cli.knowledge_accept import _extract_ids
from ...project import tool_project_id
from ...services.decision_queue import close_extract_runs
from ...services.knowledge_service import KnowledgeService
from ...ux.messages import TOOL_DESC

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_knowledge_review"
TOOL_DESCRIPTION = TOOL_DESC["review"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "待审知识条目 ID；action=skip 时为决策卡 id（extract:<run_id> 或冲突 id）",
        },
        "ids": {"type": "array", "items": {"type": "string"}, "description": "批量审批的条目 ID 列表"},
        "all_pending": {
            "type": "boolean",
            "description": (
                "true = 审批 tags 不含 bootstrap_run_id 的 pending（日常 auto-extract 等）；"
                "不含本轮 bootstrap 抽取，除非同时传 bootstrap_run_id"
            ),
        },
        "bootstrap_run_id": {
            "type": "string",
            "description": "只审批该 bootstrap 轮次的抽取（与 all_pending 联用，或单独表示本轮全部）",
        },
        "content_type": {"type": "string", "description": "批量审批时按内容类型过滤（convention/architecture/faq/documentation）"},
        "include_archived": {"type": "boolean", "description": "批量审批时包含 archived（bootstrap 限量溢出）条目"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "action": {
            "type": "string",
            "enum": ["approve", "reject", "skip"],
            "description": "approve/reject=审批；skip=抑制决策卡（不审批）",
        },
    },
    "required": ["action"],
}


async def _tags_of(knowledge: KnowledgeService, item_ids: list[str]) -> list[Any]:
    if not item_ids:
        return []
    if knowledge._store is not None:
        tags: list[Any] = []
        for item_id in item_ids:
            try:
                tags.append(list(knowledge._store.read(item_id).tags or []))
            except FileNotFoundError:
                tags.append([])
        return tags
    if knowledge._db is None:
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
    store = getattr(runtime_or_knowledge, "store", getattr(knowledge, "_store", None))
    db = getattr(runtime_or_knowledge, "db", knowledge._db)
    project_id = tool_project_id(runtime_or_knowledge, arguments)
    action = str(arguments.get("action", ""))
    if action == "skip":
        decision_id = str(arguments.get("id") or "")
        if not decision_id:
            return {"status": "error", "message": "skip 需要决策卡 id"}
        if decisions is not None:
            decisions.suppress(decision_id)
        return {"status": "ok", "id": decision_id, "suppressed": True}
    if action not in ("approve", "reject"):
        return {"status": "error", "message": "action 非法（approve/reject/skip）"}
    approve = action == "approve"
    bootstrap_run_id = arguments.get("bootstrap_run_id") or None
    if bootstrap_run_id is not None:
        bootstrap_run_id = str(bootstrap_run_id)

    ids = arguments.get("ids")
    if ids:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return {"status": "error", "message": "ids 必须是字符串数组"}
        tags = await _tags_of(knowledge, ids)
        result = await knowledge.review_batch(
            project_id, approve, ids=ids, bootstrap_run_id=bootstrap_run_id,
        )
        if result.get("processed"):
            await close_extract_runs(
                decisions, tags, db=db, store=store, project_id=project_id,
            )
        return {"status": "ok", **result}

    if arguments.get("all_pending") or (bootstrap_run_id and not arguments.get("id")):
        content_type = arguments.get("content_type") or None
        include_archived = bool(arguments.get("include_archived"))
        if bootstrap_run_id:
            extract_ids = await _extract_ids(runtime_or_knowledge, bootstrap_run_id)
            if extract_ids:
                result = await knowledge.review_batch(
                    project_id, approve,
                    ids=extract_ids,
                    content_type=content_type,
                    include_archived=include_archived,
                    bootstrap_run_id=bootstrap_run_id,
                )
            else:
                result = {
                    "processed": 0,
                    "new_status": "active" if approve else "rejected",
                }
        else:
            result = await knowledge.review_batch(
                project_id, approve,
                content_type=content_type,
                include_archived=include_archived,
                exclude_bootstrap=True,
            )
        if result.get("processed") and bootstrap_run_id:
            await close_extract_runs(
                decisions, [json.dumps([f"bootstrap_run_id:{bootstrap_run_id}"])],
                db=db, store=store, project_id=project_id,
            )
        return {"status": "ok", **result}

    item_id = str(arguments.get("id", ""))
    if not item_id:
        return {"status": "error", "message": "缺少审批目标：id / ids / all_pending 三选一"}
    tags = await _tags_of(knowledge, [item_id])
    new_status = await knowledge.review(item_id, project_id, approve=approve)
    if new_status is None:
        return {"status": "error", "message": f"条目不存在或不处于待审状态: {item_id}"}
    await close_extract_runs(
        decisions, tags, db=db, store=store, project_id=project_id,
    )
    return {"status": "ok", "id": item_id, "new_status": new_status}
