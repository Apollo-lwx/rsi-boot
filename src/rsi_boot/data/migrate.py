"""Schema 迁移执行器（§2.2）：PRAGMA user_version 记录版本，migrations/ 按序执行。

每个迁移文件命名 NNN_name.sql（如 001_init.sql），执行后将 user_version 置为 NNN。
迁移在事务内执行，失败即回滚并抛错（启动失败优于半迁移状态）。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from importlib import resources
from pathlib import Path

from .sqlite import SQLiteClient

logger = logging.getLogger(__name__)

_MIGRATION_PATTERN = re.compile(r"^(\d{3})_.+\.sql$")


def _split_statements(script: str) -> list[str]:
    """按 sqlite3.complete_statement 切分脚本（触发器 BEGIN...END 块内含分号，不能简单 split）"""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer)
            buffer = ""
    if buffer.strip():
        statements.append(buffer)
    return statements


def _load_migrations() -> list[tuple[int, str]]:
    """从包内 migrations/ 目录加载 (version, sql)，按版本号升序"""
    migrations: list[tuple[int, str]] = []
    mig_dir = resources.files("rsi_boot") / "migrations"
    for entry in sorted(mig_dir.iterdir()):
        match = _MIGRATION_PATTERN.match(entry.name)
        if match:
            migrations.append((int(match.group(1)), entry.read_text(encoding="utf-8")))
    return migrations


async def migrate(db: SQLiteClient) -> int:
    """执行所有未应用的迁移，返回当前 user_version"""
    conn = await db.connect()
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row else 0

    for version, sql in _load_migrations():
        if version <= current:
            continue
        logger.info("应用迁移 %03d ...", version)
        # executescript 会先隐式提交，破坏事务性；改为显式事务内逐条执行（DDL 可回滚）
        try:
            await conn.execute("BEGIN")
            for statement in _split_statements(sql):
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {version}")
            await conn.commit()
        except Exception:
            await conn.rollback()
            logger.exception("迁移 %03d 失败，已回滚", version)
            raise
        current = version
    return current
