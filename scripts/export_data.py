"""数据导出 CLI（§8.3）。逻辑在 rsi_boot.services.export_service（可测试）。

用法：
    python scripts/export_data.py --table interaction_logs --format json --out logs.json
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rsi_boot.bootstrap import default_db_path
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.services.export_service import EXPORTABLE_TABLES, export_table


async def _run(args: argparse.Namespace) -> int:
    db = SQLiteClient(args.db or default_db_path())
    try:
        return await export_table(args.table, args.format, args.out, db)
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI Boot 数据导出")
    parser.add_argument("--table", required=True, choices=EXPORTABLE_TABLES)
    parser.add_argument("--format", default="json", choices=["json", "csv"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--db", default=None, type=Path, help="数据库路径，缺省 ~/.rsi/rsi.db")
    args = parser.parse_args()

    count = asyncio.run(_run(args))
    print(f"已导出 {count} 条 {args.table} 到 {args.out}")


if __name__ == "__main__":
    main()
