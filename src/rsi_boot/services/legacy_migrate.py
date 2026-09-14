"""旧版 ~/.rsi/rsi.db 共享库 → 每项目库 / 全局库（Spec §10.4）。

自动：仅把「目录名」命名空间 + 全局画像（project_id=''）迁出。
不自动：`default` 与其它未匹配命名空间——那是串库水槽，须 `rsi migrate adopt`。
拷贝后源库保留作备份；认领记录写 `~/.rsi/legacy_migration.json`，避免重复灌入。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..data.migrate import migrate
from ..data.sqlite import SQLiteClient
from ..project import legacy_shared_db_path, rsi_home

logger = logging.getLogger(__name__)

#: 含 project_id 且需要按命名空间搬迁的表（顺序照顾 FK：先知识再引用它的台账）
_SCOPED_TABLES = (
    "knowledge_items",
    "interaction_logs",
    "strategy_configs",
    "harness_proposals",
    "config_snapshots",
    "project_configs",
    "extraction_candidates",
    "rule_artifacts",
    "rule_conflicts",
    "user_profiles",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal_path() -> Path:
    return rsi_home() / "legacy_migration.json"


def _load_journal() -> dict[str, Any]:
    path = _journal_path()
    if not path.is_file():
        return {"global_profiles_migrated": False, "adoptions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"global_profiles_migrated": False, "adoptions": []}
    data.setdefault("global_profiles_migrated", False)
    data.setdefault("adoptions", [])
    return data


def _save_journal(data: dict[str, Any]) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _norm_db_path(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _already_adopted(
    journal: dict[str, Any],
    namespace: str,
    dest_project_id: str,
    dest_db_path: Path | str | None = None,
) -> bool:
    want = _norm_db_path(dest_db_path) if dest_db_path else None
    for row in journal.get("adoptions") or []:
        if row.get("namespace") != namespace:
            continue
        row_db = row.get("dest_db")
        if want and row_db:
            if _norm_db_path(row_db) == want:
                return True
            continue
        if not want and row.get("dest_project_id") == dest_project_id:
            return True
    return False


async def _table_columns(db: SQLiteClient, table: str) -> list[str]:
    conn = await db.connect()
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return [r["name"] for r in rows]


async def _table_exists(db: SQLiteClient, table: str) -> bool:
    conn = await db.connect()
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cur:
        return await cur.fetchone() is not None


async def adopt_namespaces(
    src_path: Path,
    dest_db: SQLiteClient,
    namespaces: Iterable[str],
    dest_project_id: str,
) -> dict[str, int]:
    """把源库中指定 project_id 的行拷入目标库，并改写为 dest_project_id。"""
    ns = [n for n in namespaces if n != ""]
    if not ns:
        return {}
    src = SQLiteClient(src_path)
    counts: dict[str, int] = {}
    try:
        if not Path(src_path).is_file():
            return {}
        await migrate(src)
        src_conn = await src.connect()
        dest_conn = await dest_db.connect()
        placeholders = ",".join("?" for _ in ns)
        for table in _SCOPED_TABLES:
            if not await _table_exists(src, table):
                continue
            cols = await _table_columns(src, table)
            if "project_id" not in cols:
                continue
            dest_cols = await _table_columns(dest_db, table)
            use_cols = [c for c in cols if c in dest_cols]
            if "project_id" not in use_cols:
                continue
            pid_idx = use_cols.index("project_id")
            col_sql = ",".join(use_cols)
            value_sql = ",".join("?" for _ in use_cols)
            async with src_conn.execute(
                f"SELECT {col_sql} FROM {table} WHERE project_id IN ({placeholders})",
                ns,
            ) as cur:
                rows = await cur.fetchall()
            copied = 0
            for row in rows:
                values = [row[c] for c in use_cols]
                values[pid_idx] = dest_project_id
                await dest_conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_sql}) VALUES ({value_sql})",
                    values,
                )
                copied += 1
            counts[table] = copied
        await dest_conn.commit()
    finally:
        await src.close()
    journal = _load_journal()
    dest_path = dest_db.db_path
    for namespace in ns:
        if not _already_adopted(journal, namespace, dest_project_id, dest_path):
            journal["adoptions"].append({
                "namespace": namespace,
                "dest_project_id": dest_project_id,
                "dest_db": str(Path(dest_path).resolve()),
                "at": _utc_iso(),
                "rows": counts,
            })
    _save_journal(journal)
    logger.info("遗留命名空间 %s 已认领到 %s：%s", ns, dest_project_id, counts)
    return counts


async def migrate_global_profiles(src_path: Path, global_db: SQLiteClient) -> int:
    """把共享库中 project_id='' 的画像写入 global.db（只做一次）"""
    journal = _load_journal()
    if journal.get("global_profiles_migrated"):
        return 0
    if not Path(src_path).is_file():
        journal["global_profiles_migrated"] = True
        _save_journal(journal)
        return 0
    src = SQLiteClient(src_path)
    copied = 0
    try:
        await migrate(src)
        if not await _table_exists(src, "user_profiles"):
            journal["global_profiles_migrated"] = True
            _save_journal(journal)
            return 0
        src_conn = await src.connect()
        dest_conn = await global_db.connect()
        async with src_conn.execute(
            "SELECT user_id, project_id, profile_data, created_at, updated_at "
            "FROM user_profiles WHERE project_id = ''",
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            await dest_conn.execute(
                "INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at) "
                "VALUES (?, '', ?, ?, ?) "
                "ON CONFLICT(user_id, project_id) DO UPDATE SET "
                "profile_data = excluded.profile_data, updated_at = excluded.updated_at",
                (row["user_id"], row["profile_data"], row["created_at"], row["updated_at"]),
            )
            copied += 1
        await dest_conn.commit()
    finally:
        await src.close()
    journal["global_profiles_migrated"] = True
    _save_journal(journal)
    if copied:
        logger.info("已将 %d 份全局画像迁入 global.db", copied)
    return copied


async def list_legacy_namespaces(src_path: Optional[Path] = None) -> dict[str, int]:
    """遗留共享库里各 project_id 的知识条数（供 rsi migrate status）"""
    path = Path(src_path or legacy_shared_db_path())
    if not path.is_file():
        return {}
    src = SQLiteClient(path)
    try:
        await migrate(src)
        conn = await src.connect()
        async with conn.execute(
            "SELECT project_id, COUNT(*) AS n FROM knowledge_items GROUP BY project_id"
        ) as cur:
            rows = await cur.fetchall()
        return {str(r["project_id"] or ""): int(r["n"]) for r in rows}
    finally:
        await src.close()


async def remap_to_workspace_id(db: SQLiteClient, dest_project_id: str) -> int:
    """把工作区库里旧哈希/目录名 project_id 收成 local（空字符串全局行不动）。

    已有 dest 行时丢掉旧哈希行，避免 PRIMARY KEY / UNIQUE 把启动路径打崩。
    """
    conn = await db.connect()
    updated = 0
    for table in _SCOPED_TABLES:
        if not await _table_exists(db, table):
            continue
        cols = await _table_columns(db, table)
        if "project_id" not in cols:
            continue
        await _drop_colliding_before_remap(conn, table, dest_project_id, cols)
        cur = await conn.execute(
            f"UPDATE {table} SET project_id = ? "
            f"WHERE project_id IS NOT NULL AND project_id != '' AND project_id != ?",
            (dest_project_id, dest_project_id),
        )
        updated += int(cur.rowcount or 0)
    await conn.commit()
    return updated


async def _drop_colliding_before_remap(
    conn: Any,
    table: str,
    dest_project_id: str,
    cols: list[str],
) -> None:
    if table == "user_profiles" and "user_id" in cols:
        async with conn.execute(
            "SELECT user_id FROM user_profiles WHERE project_id = ?",
            (dest_project_id,),
        ) as cur:
            keep = {row["user_id"] for row in await cur.fetchall()}
        for user_id in keep:
            await conn.execute(
                "DELETE FROM user_profiles WHERE user_id = ? AND project_id != ? AND project_id != ''",
                (user_id, dest_project_id),
            )
        return
    if table == "project_configs":
        async with conn.execute(
            "SELECT 1 FROM project_configs WHERE project_id = ?",
            (dest_project_id,),
        ) as cur:
            if await cur.fetchone() is None:
                return
        await conn.execute(
            "DELETE FROM project_configs WHERE project_id != ? AND project_id != ''",
            (dest_project_id,),
        )


async def maybe_auto_migrate(
    project_root: Path,
    dest_project_id: str,
    dest_db: SQLiteClient,
) -> dict[str, Any]:
    """启动时按目录名认领旧共享库命名空间。永不自动认领 default，也不灌全局画像。"""
    legacy = legacy_shared_db_path()
    report: dict[str, Any] = {"legacy_db": str(legacy), "auto_namespaces": [], "counts": {}}
    if not legacy.is_file():
        return report
    folder = Path(project_root).name
    journal = _load_journal()
    auto: list[str] = []
    if folder and folder != "default" and not _already_adopted(
        journal, folder, dest_project_id, dest_db.db_path,
    ):
        auto.append(folder)
    if auto:
        report["auto_namespaces"] = auto
        report["counts"] = await adopt_namespaces(legacy, dest_db, auto, dest_project_id)
    return report
