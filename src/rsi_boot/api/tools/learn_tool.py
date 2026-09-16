"""rsi_learn: Catch→Teach→Fix then teach_record. RSI does not write the lesson."""

from __future__ import annotations

from typing import Any

from rsi_boot.learning.audit_store import (
    audit_finish,
    audit_probes,
    audit_report,
    audit_start,
)
from rsi_boot.learning.gene_map import extract_items
from rsi_boot.learning.promote import promote_items
from rsi_boot.learning.reading_pack_actions import pack_done, pack_list, pack_open
from rsi_boot.learning.teaching import (
    RUBRIC_TEXT,
    resolve_lang,
    skip_draft,
    teach_catch,
    teach_record,
)
from rsi_boot.ux.messages import TOOL_DESC, t

TOOL_NAME = "rsi_learn"
TOOL_DESCRIPTION = TOOL_DESC["learn"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": (
                "teach_catch / teach_record / skip / rubric / "
                "audit_start / audit_probes / audit_report / audit_finish / extract / promote / "
                "pack_list / pack_open / pack_done"
            ),
        },
        "lang": {"type": "string", "enum": ["zh", "en"], "description": "explicit locale override"},
        "id": {"type": "string", "description": "teach-draft id or reading-pack id"},
        "pack_id": {"type": "string", "description": "alias of id for pack_open / pack_done"},
        "lane": {
            "type": "string",
            "description": (
                "pack_list filter: skills_rules / teaching / docs / code / "
                "git / conversation / cursor / other"
            ),
        },
        "status": {"type": "string", "description": "pack_done: done / skipped / pending"},
        "reopen_skipped": {
            "type": "boolean",
            "description": "pack_list: true = 把不允许 skip 的 skipped 包改回 pending",
        },
        "draft_id": {"type": "string", "description": "alias of id"},
        "trigger": {"type": "object", "description": "teach_catch trigger"},
        "system_attempts": {"type": "array", "description": "teach_catch attempts"},
        "lesson": {"type": "object", "description": "host-written lesson; correct_fix required for teach_record"},
        "reason": {"type": "string", "description": "skip reason"},
        "low_confidence": {"type": "boolean"},
        "promote_to_pattern": {"type": "boolean"},
        "payload": {"type": "object"},
        "scope": {"type": "string", "description": "audit/extract scope: session or full"},
        "scope_id": {"type": "string", "description": "required when scope=full"},
        "changed_files": {"type": "array", "description": "host ATS file list for audit_start"},
        "globs": {"type": "array", "description": "host ATS globs for audit_start"},
        "probe_results": {"type": "array", "description": "uploaded probe results; RSI does not exec"},
        "findings": {"type": "array", "description": "host findings for audit_report"},
        "overall": {"type": "string", "description": "pass or fail"},
        "probe_summary": {"type": "object"},
        "disclaimer_partial": {"type": "boolean"},
        "items": {"type": "array", "description": "classified closeout items for extract"},
    },
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    lang = resolve_lang(arguments)
    action = str(arguments.get("action") or "").strip()
    if not action:
        return {"status": "error", "code": "invalid", "message": t("LEARN_NEED_ACTION", lang)}
    if action == "pack_list":
        return pack_list(
            runtime.store,
            lang,
            lane=str(arguments.get("lane") or ""),
            reopen_skipped=bool(arguments.get("reopen_skipped")),
        )
    if action == "pack_open":
        return pack_open(
            runtime.store,
            str(arguments.get("id") or arguments.get("pack_id") or ""),
            lang,
        )
    if action == "pack_done":
        return pack_done(
            runtime.store,
            str(arguments.get("id") or arguments.get("pack_id") or ""),
            status=str(arguments.get("status") or "done"),
            reason=str(arguments.get("reason") or ""),
            lang=lang,
        )
    if action == "promote":
        return promote_items(runtime.store, arguments, lang)
    if action == "audit_start":
        return audit_start(runtime.store, arguments, lang)
    if action == "audit_probes":
        return audit_probes(runtime.store, arguments, lang)
    if action == "audit_report":
        return audit_report(runtime.store, arguments, lang)
    if action == "audit_finish":
        return audit_finish(runtime.store, arguments, lang)
    if action == "extract":
        out = extract_items(runtime.store, arguments, lang)
        if out.get("status") == "success":
            runtime.invalidate_index()
        return out
    if action.startswith("audit_"):
        return {"status": "error", "code": "not_in_phase", "message": t("PHASE_AUDIT", lang)}
    if action == "teach_catch":
        return teach_catch(runtime.store, arguments, lang)
    if action == "teach_record":
        out = teach_record(runtime.store, arguments, lang)
        if out.get("status") == "success":
            runtime.invalidate_index()
        return out
    if action == "skip":
        return skip_draft(runtime.store, arguments, lang)
    if action == "rubric":
        return {
            "status": "success",
            "message": t("EXTRACT_CLOSEOUT", lang),
            "data": {"rubric": RUBRIC_TEXT},
        }
    return {"status": "error", "code": "invalid", "message": t("LEARN_NEED_ACTION", lang)}
