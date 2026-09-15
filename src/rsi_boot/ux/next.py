"""Superpowers-style numbered next-step envelope for host/CLI interaction."""

from __future__ import annotations

from typing import Any

from rsi_boot.ux.messages import t


def next_block(
    options: list[dict[str, Any]],
    *,
    recommended: str = "1",
    lang: str = "zh",
    prompt: str | None = None,
) -> dict[str, Any]:
    numbered: list[dict[str, Any]] = []
    for i, opt in enumerate(options, start=1):
        row = dict(opt)
        row.setdefault("id", str(i))
        numbered.append(row)
    return {
        "prompt": prompt or t("NEXT_PROMPT", lang),
        "options": numbered,
        "recommended": recommended,
    }


def render_next(block: dict[str, Any], lang: str = "zh") -> list[str]:
    lines = ["", str(block.get("prompt") or t("NEXT_PROMPT", lang)), ""]
    rec = str(block.get("recommended") or "1")
    for opt in block.get("options") or []:
        suffix = t("NEXT_RECOMMENDED", lang) if str(opt.get("id")) == rec else ""
        lines.append(f"{opt.get('id')}. {opt.get('label', '')}{suffix}")
    lines.extend(["", t("NEXT_WHICH", lang)])
    return lines
