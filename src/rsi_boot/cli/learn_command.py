"""rsi learn: teach_catch / teach_record / skip / rubric. Same semantics as MCP."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.api.tools import learn_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.project import resolve_project_root
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsi learn")
    parser.add_argument("--lang", choices=["zh", "en"], default=None)
    parser.add_argument("--project-root", default=None)
    parser.add_argument("action", nargs="?", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--id", default=None)
    return parser


def _pop_lang(argv: list[str] | None) -> tuple[list[str] | None, str]:
    lang = locale_lang()
    if argv is None:
        return None, lang
    cleaned: list[str] = []
    i = 0
    seen = False
    while i < len(argv):
        tok = argv[i]
        if tok == "--lang" and i + 1 < len(argv) and argv[i + 1] in ("zh", "en"):
            lang = argv[i + 1]
            seen = True
            i += 2
            continue
        if tok.startswith("--lang="):
            val = tok.split("=", 1)[1]
            if val in ("zh", "en"):
                lang = val
                seen = True
                i += 1
                continue
        cleaned.append(tok)
        i += 1
    if seen:
        os.environ["RSI_LANG"] = lang
    return cleaned, lang


def _load_file(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _arguments(args: argparse.Namespace, lang: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": args.action, "lang": lang}
    if args.id:
        payload["id"] = args.id
    if not args.file:
        return payload
    path = Path(args.file)
    if not path.is_file():
        raise FileNotFoundError(path)
    loaded = _load_file(path)
    if args.action == "teach_record":
        if "lesson" in loaded and isinstance(loaded["lesson"], dict):
            payload.update(loaded)
            payload["action"] = "teach_record"
            payload["lang"] = lang
        else:
            payload["lesson"] = loaded
        return payload
    payload.update(loaded)
    payload["action"] = args.action
    payload["lang"] = lang
    return payload


def _exit_for(result: dict[str, Any], lang: str) -> int:
    status = result.get("status")
    message = str(result.get("message") or "")
    if status == "success":
        if message:
            print(message)
        return 0
    if message:
        print(message, file=sys.stderr)
    code = result.get("code")
    if code == "not_found":
        return 3
    if code in ("invalid", "not_in_phase"):
        return 2
    return 1


async def _dispatch(args: argparse.Namespace, lang: str) -> int:
    root = resolve_project_root(
        explicit=Path(args.project_root) if args.project_root else None,
    )
    try:
        arguments = _arguments(args, lang)
    except FileNotFoundError as exc:
        print(t("NOT_FOUND", lang, id=str(exc)), file=sys.stderr)
        return 3
    runtime = await build_runtime(project_root=root)
    try:
        result = await learn_tool.handle(runtime, arguments)
        return _exit_for(result, lang)
    finally:
        await runtime.close()


def run_learn(argv: list[str] | None = None) -> int:
    cleaned, lang = _pop_lang(list(argv) if argv is not None else argv)
    try:
        args = _parser().parse_args(cleaned)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2

    if getattr(args, "lang", None) in ("zh", "en"):
        lang = args.lang
        os.environ["RSI_LANG"] = lang

    if not args.action:
        print(t("LEARN_NEED_ACTION", lang), file=sys.stderr)
        return 2
    try:
        return asyncio.run(_dispatch(args, lang))
    except Exception:
        return 1
