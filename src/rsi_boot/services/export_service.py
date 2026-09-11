"""数据导出服务（§8.3）：交互日志/知识条目/画像导出为 JSON 或 CSV。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..data.sqlite import SQLiteClient

EXPORTABLE_TABLES = ("interaction_logs", "knowledge_items", "user_profiles")


async def export_table(table: str, fmt: str, out: Path, db: SQLiteClient) -> int:
    """导出指定表到文件，返回行数。表名白名单校验防注入"""
    if table not in EXPORTABLE_TABLES:
        raise ValueError(f"不支持导出的表: {table}（可选 {EXPORTABLE_TABLES}）")
    conn = await db.connect()
    async with conn.execute(f"SELECT * FROM {table}") as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif fmt == "csv":
        if rows:
            with out.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        else:
            out.write_text("", encoding="utf-8")
    else:
        raise ValueError(f"不支持的格式: {fmt}")
    return len(rows)
