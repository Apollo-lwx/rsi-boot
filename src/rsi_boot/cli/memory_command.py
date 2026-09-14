"""rsi memory: migrate / index / open / reindex / graph."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

from rsi_boot.cli.progress import Progress
from rsi_boot.memory.graph import render_mermaid
from rsi_boot.memory.store import MemoryStore
from rsi_boot.project import resolve_project_root
from rsi_boot.rag.index import build_index
from rsi_boot.services.memory_migrate import MemoryMigrateHalfError, migrate_workspace
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsi memory")
    parser.add_argument("--lang", choices=["zh", "en"], default=None)
    parser.add_argument("--project-root", default=None)
    sub = parser.add_subparsers(dest="action")

    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.add_argument("--json", dest="as_json", action="store_true")
    p_mig.add_argument("--project-root", default=None)
    p_mig.add_argument("--lang", choices=["zh", "en"], default=None)

    p_idx = sub.add_parser("index")
    p_idx.add_argument("--json", dest="as_json", action="store_true")
    p_idx.add_argument("--lang", choices=["zh", "en"], default=None)
    p_idx.add_argument("--project-root", default=None)

    p_open = sub.add_parser("open")
    p_open.add_argument("id")
    p_open.add_argument("--lang", choices=["zh", "en"], default=None)
    p_open.add_argument("--project-root", default=None)

    p_re = sub.add_parser("reindex")
    p_re.add_argument("--lang", choices=["zh", "en"], default=None)
    p_re.add_argument("--project-root", default=None)

    p_graph = sub.add_parser("graph")
    p_graph.add_argument("--lang", choices=["zh", "en"], default=None)
    p_graph.add_argument("--project-root", default=None)
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


def _root(args: argparse.Namespace) -> Path:
    explicit = Path(args.project_root) if getattr(args, "project_root", None) else None
    return resolve_project_root(explicit=explicit)


def _store(root: Path) -> MemoryStore:
    rsi = root / ".rsi"
    rsi.mkdir(parents=True, exist_ok=True)
    return MemoryStore(rsi)


def _cmd_index(args: argparse.Namespace) -> int:
    store = _store(_root(args))
    items = [
        {
            "id": doc.id,
            "type": doc.type,
            "status": doc.status,
            "title": doc.title,
            "path": doc.path,
        }
        for doc in store.list_all()
    ]
    if getattr(args, "as_json", False):
        print(json.dumps(items, ensure_ascii=False))
        return 0
    print("id  type  status  title  path")
    for row in items:
        print(f"{row['id']}  {row['type']}  {row['status']}  {row['title']}  {row['path']}")
    return 0


def _cmd_open(args: argparse.Namespace, lang: str) -> int:
    store = _store(_root(args))
    doc_id = str(args.id or "").strip().lower()
    try:
        doc = store.read(doc_id)
    except FileNotFoundError:
        print(t("NOT_FOUND", lang, id=doc_id), file=sys.stderr)
        return 3
    if doc.path:
        path = store.rsi_dir / doc.path
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            print(text, end="" if text.endswith("\n") else "\n")
            return 0
    print(
        yaml.safe_dump(doc.model_dump(exclude_none=True), allow_unicode=True, sort_keys=False),
        end="",
    )
    return 0


def _cmd_reindex(args: argparse.Namespace, lang: str) -> int:
    store = _store(_root(args))
    docs = [doc for doc in store.list_official() if doc.status == "active"]
    index = build_index(docs)
    cache = store.rsi_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "inverted.json").write_text(
        json.dumps(index, ensure_ascii=False),
        encoding="utf-8",
    )
    print(t("REINDEX_DONE", lang))
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    store = _store(_root(args))
    mermaid = render_mermaid(store.rsi_dir, store)
    print(mermaid, end="" if mermaid.endswith("\n") else "\n")
    return 0


def _cmd_migrate(args: argparse.Namespace, lang: str) -> int:
    root = _root(args)
    db_path = root / ".rsi" / "rsi.db"
    if not db_path.is_file():
        print(t("MIGRATE_NO_DB", lang), file=sys.stderr)
        return 3

    stream = sys.stderr if args.as_json else sys.stdout
    progress = Progress(stream=stream)
    try:
        counts = asyncio.run(
            migrate_workspace(
                root,
                dry_run=bool(args.dry_run),
                progress=progress,
                lang=lang,
            )
        )
    except MemoryMigrateHalfError:
        print(t("MIGRATE_HALF", lang), file=sys.stderr)
        return 3
    except FileNotFoundError:
        print(t("MIGRATE_NO_DB", lang), file=sys.stderr)
        return 3
    except Exception:
        print(t("MIGRATE_HALF", lang), file=sys.stderr)
        return 3

    if args.as_json:
        print(json.dumps(counts, ensure_ascii=False, default=str))
    return 0


def run_memory(argv: list[str] | None = None) -> int:
    cleaned, lang = _pop_lang(list(argv) if argv is not None else argv)
    try:
        args = _parser().parse_args(cleaned)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2

    if getattr(args, "lang", None) in ("zh", "en"):
        lang = args.lang
        os.environ["RSI_LANG"] = lang

    if not args.action:
        print(t("MEMORY_NEED_SUB", lang), file=sys.stderr)
        return 2
    if args.action == "graph":
        return _cmd_graph(args)
    if args.action == "migrate":
        return _cmd_migrate(args, lang)
    if args.action == "index":
        return _cmd_index(args)
    if args.action == "open":
        return _cmd_open(args, lang)
    if args.action == "reindex":
        return _cmd_reindex(args, lang)
    print(t("MEMORY_NEED_SUB", lang), file=sys.stderr)
    return 2
