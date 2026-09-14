"""rsi memory: migrate (this wave); reindex/index/open/graph later."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rsi_boot.cli.progress import Progress
from rsi_boot.project import resolve_project_root
from rsi_boot.services.memory_migrate import MemoryMigrateHalfError, migrate_workspace
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsi memory")
    sub = parser.add_subparsers(dest="action")
    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.add_argument("--json", dest="as_json", action="store_true")
    p_mig.add_argument("--project-root", default=None)
    return parser


def run_memory(argv: list[str] | None = None) -> int:
    lang = locale_lang()
    args = _parser().parse_args(argv)
    if args.action != "migrate":
        print(t("MEMORY_NEED_SUB", lang), file=sys.stderr)
        return 2

    explicit = Path(args.project_root) if args.project_root else None
    root = resolve_project_root(explicit=explicit)
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
