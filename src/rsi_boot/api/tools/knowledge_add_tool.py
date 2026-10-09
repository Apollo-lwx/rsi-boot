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
        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
        "roles": {"type": "array", "items": {"type": "string"}, "description": "适用角色，空=通用"},
        "domain": {"type": "string", "description": "领域"},
        "source_url": {"type": "string", "description": "来源（文档路径/signal），带来源的 active 落正式"},
        "pack_id": {"type": "string", "description": "阅读包 id；蒸馏时必填，写入 pack_id: 标签"},
        "content_type": {
            "type": "string",
            "description": "prohibition/convention/skill/documentation 或案例类型",
        },
        "trigger_condition": {
            "type": "string",
            "description": "前提条件：这条记忆在什么前提下成立（如「仅当使用 MyBatis 时」）",
        },
        "expiry_condition": {
            "type": "string",
            "description": "失效条件：什么情况下该记忆应视为过时（如「迁移到 JPA 后作废」）",
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
    pack_inherited_domain = ""
    if pack_id:
        marker = f"pack_id:{pack_id}"
        if marker not in tags:
            tags.append(marker)
        if "signal:distilled" not in tags:
            tags.append("signal:distilled")
        if store is not None:
            from ...scanner.reading_packs import distill_type_for_pack, load_index, load_pack

            pack = load_pack(store.rsi_dir, pack_id)
            if pack is not None:
                if content_type != "prohibition":
                    content_type = distill_type_for_pack(pack)
                # 蒸馏条从 pack 直接继承板块域；宿主显式 domain 优先
                if not str(arguments.get("domain") or "").strip():
                    pack_domain = str(pack.get("domain") or "").strip()
                    if pack_domain:
                        pack_inherited_domain = pack_domain
                run_id = load_index(store.rsi_dir).get("bootstrap_run_id")
                if run_id and not any(str(tag).startswith("bootstrap_run_id:") for tag in tags):
                    tags.append(f"bootstrap_run_id:{run_id}")
    if not content_type:
        content_type = "documentation"
    source_url = str(arguments.get("source_url") or "").strip() or None
    domain = str(arguments.get("domain") or "").strip() or None
    if domain is None:
        # 阶段2：宿主未显式给域时补板块域，让新增条目也能进层级知识树
        # 优先取 pack 继承；否则按 bootstrap 同源规则从 source_url 推断
        domain = pack_inherited_domain or None
        if domain is None and source_url and not pack_id:
            from ...scanner.reading_packs import infer_domain

            root = getattr(knowledge, "_project_root", None)
            domain = infer_domain(source_url, root=root) or None
    item = KnowledgeItem(
        project_id=str(arguments.get("project_id") or ""),
        title=title,
        content=content,
        content_type=content_type,
        tags=tags,
        roles=[str(r) for r in arguments.get("roles") or []],
        domain=domain,
        source_url=source_url,
        trigger_condition=str(arguments.get("trigger_condition") or "").strip() or None,
        expiry_condition=str(arguments.get("expiry_condition") or "").strip() or None,
    )
    result = await knowledge.add(item)
    if isinstance(result, dict) and result.get("status") == "error":
        return result
    if isinstance(result, dict):
        payload = {"status": "success", **result}
    else:
        payload = {"status": "success", "id": result}
    # 近重复提示（改进点 3）：非蒸馏批量写入时，用新条目在同类型正式记忆里找近邻，
    # 命中则随响应回传候选，交由宿主用 rsi_conflicts / rsi_knowledge_review 裁决。不阻断写入。
    if store is not None and not pack_id:
        added_id = payload.get("id")
        if isinstance(added_id, str):
            try:
                from ...learning.dedup import find_near_duplicates

                added = store.read(added_id)
                dups = find_near_duplicates(
                    store, title=added.title, content=added.content,
                    content_type=added.type, exclude_id=added.id,
                )
                if dups:
                    payload["possible_duplicates"] = dups
            except FileNotFoundError:
                pass
    return payload
