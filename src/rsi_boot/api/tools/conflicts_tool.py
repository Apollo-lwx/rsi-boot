"""rsi_conflicts 工具（Spec v3.1 §4.9/§6）：用户手写规则与学习记忆的冲突查询与裁决。"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "rsi_conflicts"

TOOL_DESCRIPTION = (
    "查询疑似冲突并对冲突做出裁决。三类："
    "① 学习记忆 vs 手写规则（contradiction/stale/overlap）——"
    "user_wins=以你的规则为准（学习记忆不再注入）、memory_wins=以学习记忆为准"
    "（返回规则文件位置请手动修改）、coexist=两者共存不再提醒；"
    "② 知识对（version/doc_code/incoherent）——"
    "keep_item=保留 item_id 侧并归档对侧 source_url、keep_peer=保留 user_rule_path 侧、"
    "coexist=两侧转/保持可用且不再提醒；"
    "③ explain（conflict_id 必填）=只读展开两侧摘录与各 option 影响，不改库。"
    "用户要展开说明时先 explain，不要关掉抉择卡。"
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "resolve", "scan", "explain"],
                   "description": "list=查询冲突（默认） resolve=裁决 scan=立即扫描 explain=只读展开"},
        "project_id": {"type": "string", "maxLength": 128,
                       "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "status": {"type": "string", "default": "open",
                   "description": "list 过滤：open/user_wins/memory_wins/keep_peer/keep_item/coexist/closed/all"},
        "conflict_id": {"type": "string", "description": "resolve/explain 必填：冲突 id"},
        "resolution": {
            "type": "string",
            "enum": ["user_wins", "memory_wins", "coexist", "keep_peer", "keep_item"],
            "description": "resolve 必填：规则冲突用 user_wins/memory_wins/coexist；"
                           "version/doc_code/incoherent 用 keep_peer/keep_item/coexist",
        },
        "note": {"type": "string", "maxLength": 500, "description": "resolve 可选：裁决备注"},
    },
    "required": ["action"],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    detector = runtime.conflict_detector
    from ...project import tool_project_id

    project_id = tool_project_id(runtime, arguments)
    action = str(arguments.get("action") or "list")

    if action == "scan":
        return {"status": "success", "data": await detector.scan(project_id)}
    if action == "list":
        items = await detector.list_conflicts(project_id, status=str(arguments.get("status") or "open"))
        return {"status": "success", "data": {"conflicts": items, "count": len(items)}}
    if action == "explain":
        conflict_id = str(arguments.get("conflict_id") or "")
        if not conflict_id:
            return {"status": "error", "message": "explain 需要 conflict_id"}
        data = await detector.explain(conflict_id)
        if data is None:
            return {"status": "error", "message": "冲突不存在"}
        return {"status": "success", "data": data}
    if action == "resolve":
        conflict_id = str(arguments.get("conflict_id") or "")
        resolution = str(arguments.get("resolution") or "")
        result = await detector.resolve(conflict_id, resolution, str(arguments.get("note") or ""))
        if result is None:
            return {"status": "error",
                    "message": "冲突不存在/已关闭，或 resolution 非法"
                               "（规则冲突 user_wins/memory_wins/coexist；"
                               "version/doc_code/incoherent 用 keep_item/keep_peer/coexist）"}
        return {"status": "success", "data": result}
    return {"status": "error", "message": f"未知 action: {action}"}
