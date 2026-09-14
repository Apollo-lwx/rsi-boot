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
                "audit_start / audit_probes / audit_report / audit_finish / extract "
                "(promote returns not_in_phase)"
            ),
        },
        "lang": {"type": "string", "enum": ["zh", "en"], "description": "explicit locale override"},
        "id": {"type": "string", "description": "teach-draft id for skip / teach_record"},
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
    if action == "promote":
        return {"status": "error", "code": "not_in_phase", "message": t("PHASE_PROMOTE", lang)}
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
