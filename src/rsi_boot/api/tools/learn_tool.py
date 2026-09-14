"""rsi_learn: Catch→Teach→Fix then teach_record. RSI does not write the lesson."""

from __future__ import annotations

from typing import Any

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
            "description": "teach_catch / teach_record / skip / rubric (others return not_in_phase)",
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
    },
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    lang = resolve_lang(arguments)
    action = str(arguments.get("action") or "").strip()
    if not action:
        return {"status": "error", "message": t("LEARN_NEED_ACTION", lang)}
    if action == "promote":
        return {"status": "error", "code": "not_in_phase", "message": t("PHASE_PROMOTE", lang)}
    if action.startswith("audit_") or action == "extract":
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
    return {"status": "error", "message": t("LEARN_NEED_ACTION", lang)}
