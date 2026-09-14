"""rsi_memory: index / open / reindex. graph is not in this phase."""

from __future__ import annotations

from typing import Any

from rsi_boot.learning.teaching import resolve_lang
from rsi_boot.ux.messages import TOOL_DESC, t

TOOL_NAME = "rsi_memory"
TOOL_DESCRIPTION = TOOL_DESC["memory"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "index / open / reindex (graph returns not_in_phase)",
        },
        "id": {"type": "string", "description": "exact 32-hex document id for open"},
        "lang": {"type": "string", "enum": ["zh", "en"], "description": "explicit locale override"},
    },
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    lang = resolve_lang(arguments)
    action = str(arguments.get("action") or "").strip()
    if action == "graph":
        return {"status": "error", "code": "not_in_phase", "message": t("PHASE_GRAPH", lang)}
    if action == "open":
        doc_id = str(arguments.get("id") or "").strip().lower()
        try:
            doc = runtime.store.read(doc_id)
        except FileNotFoundError:
            return {"status": "error", "code": "not_found", "message": t("NOT_FOUND", lang, id=doc_id)}
        data = doc.model_dump(exclude_none=True)
        return {
            "status": "success",
            "message": t("MEMORY_OPENED", lang, id=doc.id),
            "data": data,
        }
    if action == "index":
        items = [
            {"id": d.id, "type": d.type, "title": d.title, "path": d.path}
            for d in runtime.store.list_all()
        ]
        return {
            "status": "success",
            "message": t("MEMORY_INDEX", lang, n=len(items)),
            "data": {"items": items},
        }
    if action == "reindex":
        runtime.invalidate_index()
        index = runtime.invert_index()
        return {
            "status": "success",
            "message": t("REINDEX_DONE", lang),
            "data": {"n": index.get("n", 0)},
        }
    return {"status": "error", "code": "invalid", "message": t("MEMORY_NEED_SUB", lang)}
