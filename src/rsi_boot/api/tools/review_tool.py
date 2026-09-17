"""rsi_review 工具（§3.9/§7.1）：Harness 改进提案与配置快照的人工门径。

action：
- list             提案列表（可选 status 过滤）
- approve          确认 approved 提案 → 晋升 active（应用槽位变更 + 快照 + 观察期）
- reject           人工否决（proposed/validating/approved → rejected）
- rollback         回滚 active 提案 → 切换晋升前快照
- generate         按需触发提案生成（近 7 天负信号挖掘，仍受单轮 ≤ 5 上限约束）
- snapshot_list    配置快照版本列表
- snapshot_switch  切换到指定快照版本（任意历史版本可切换）
- snapshot_export  导出快照为目录型 bundle（genome.json + 槽位文件）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...ux.messages import TOOL_DESC

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_review"
TOOL_DESCRIPTION = TOOL_DESC["harness"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "approve", "reject", "rollback", "generate",
                     "snapshot_list", "snapshot_switch", "snapshot_export"],
            "description": "操作类型",
        },
        "proposal_id": {"type": "string", "description": "提案 ID（approve/reject/rollback）"},
        "status": {"type": "string", "description": "list 状态过滤"},
        "reason": {"type": "string", "description": "reject/rollback 理由"},
        "version": {"type": "integer", "description": "快照版本（snapshot_switch/export）"},
        "out_dir": {"type": "string", "description": "bundle 导出目录（snapshot_export）"},
    },
    "required": ["action"],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action", ""))
    from ...project import tool_project_id

    project_id = tool_project_id(runtime, arguments)
    engine = runtime.proposal_engine
    snapshots = runtime.snapshots

    if action == "list":
        items = await engine.list_proposals(project_id, status=arguments.get("status") or None)
        return {"status": "success", "proposals": items}
    if action == "generate":
        ids = await engine.generate(project_id)
        return {"status": "success", "generated": len(ids), "proposal_ids": ids}

    if action in ("approve", "reject", "rollback"):
        proposal_id = str(arguments.get("proposal_id", ""))
        if not proposal_id:
            return {"status": "error", "message": f"{action} 需要 proposal_id"}
        reason = str(arguments.get("reason", ""))
        if action == "approve":
            ok = await engine.approve(proposal_id)
        elif action == "reject":
            ok = await engine.reject(proposal_id, reason=reason)
        else:
            ok = await engine.rollback(proposal_id, reason=reason)
        if not ok:
            return {"status": "error", "message": "提案不存在或状态不允许该操作"}
        return {"status": "success", "proposal_id": proposal_id, "action": action}

    if action == "snapshot_list":
        return {"status": "success", "snapshots": await snapshots.list(project_id)}
    if action in ("snapshot_switch", "snapshot_export"):
        version = arguments.get("version")
        if version is None:
            return {"status": "error", "message": f"{action} 需要 version"}
        if action == "snapshot_switch":
            ok = await snapshots.switch(project_id, int(version))
            return {"status": "success" if ok else "error",
                    "message": f"已切换到 v{version}" if ok else f"版本不存在: v{version}"}
        out = Path(str(arguments.get("out_dir") or "./genome-bundle"))
        result = await snapshots.export_bundle(project_id, int(version), out)
        if result is None:
            return {"status": "error", "message": f"版本不存在: v{version}"}
        return {"status": "success", "bundle": str(result)}

    return {"status": "error", "message": f"未知 action: {action}"}
