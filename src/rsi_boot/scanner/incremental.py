"""增量学习与持续监听（§10.9.5/§10.9.10/§10.9.13，P2.6）。

增量学习：manifest 指纹对比（bootstrap_command 主流程）+ 信号源删除归档
（manifest 中存在但当前文件树缺失 → 对应知识条目标记 archived，不删除）。
变更收敛（§10.9.10「同一信号已变」）：ingest_document 按 source 三分支
（保留/复活/写入）+ 旧版本归档；reconcile_scope 供聚合类生成知识按标签收敛。
静默学习：rsi serve --watch 的文件监听模式——watchfiles 监听项目目录，
文档变化防抖后增量重切片写入（同一 ingest_document 逻辑收敛）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .document_scanner import chunk_kwargs_from_config, slice_document
from .signal_discovery import EXCLUDED_DIRS
from .validator import MIN_TOKENS, DedupSet, content_hash, read_text_tolerant, validate_chunk

logger = logging.getLogger(__name__)

_WATCH_EXTS = {".md", ".rst", ".txt", ".adoc"}
_DEBOUNCE_S = 2.0

#: 用户已裁决的状态（rejected/suppressed）：内容再现时尊重裁决——不重写、不复活
_DECIDED_STATUSES = ("rejected", "suppressed")


async def archive_missing_signals(
    runtime: Any,
    project_id: str,
    manifest: Dict[str, str],
    current_files: Set[str],
) -> int:
    """信号源删除 → 对应知识条目 archived（§10.9.10；--purge 彻底删除留 CLI 后续扩展）。
    active 与 pending_review 一并归档——源头已删的草稿留在审批队列无意义"""
    missing = [rel for rel in manifest if rel not in current_files]
    if not missing:
        return 0
    conn = await runtime.db.connect()
    archived = 0
    for rel in missing:
        cur = await conn.execute(
            "UPDATE knowledge_items SET status = 'archived', updated_at = ? "
            "WHERE project_id = ? AND source_url = ? AND status IN ('active', 'pending_review')",
            (datetime.now(timezone.utc).isoformat(), project_id, rel),
        )
        archived += cur.rowcount
    await conn.commit()
    if archived:
        logger.info("信号源删除归档：%d 条知识标记 archived", archived)
    return archived


async def reconcile_scope(
    runtime: Any,
    project_id: str,
    scope_sql: str,
    scope_params: List[Any],
    kept_hashes: Set[str],
) -> int:
    """范围内未被本次内容命中的 active/pending_review 条目 → archived。

    供聚合类生成知识（signal:code/config/git/correlation/rules）按标签收敛：
    内容随开发演进而变化时，旧版本不留滞（用户已裁决的 rejected/suppressed 不动）。
    """
    conn = await runtime.db.connect()
    async with conn.execute(
        f"SELECT id, content FROM knowledge_items "
        f"WHERE project_id = ? AND status IN ('active', 'pending_review') AND {scope_sql}",
        (project_id, *scope_params),
    ) as cur:
        rows = await cur.fetchall()
    stale = [r["id"] for r in rows if content_hash(r["content"]) not in kept_hashes]
    if not stale:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, len(stale), 500):  # SQLite 宿主变量上限 999，分批
        chunk = stale[i:i + 500]
        await conn.execute(
            f"UPDATE knowledge_items SET status = 'archived', updated_at = ? "
            f"WHERE id IN ({','.join('?' for _ in chunk)})",
            [now, *chunk],
        )
    await conn.commit()
    if stale:
        logger.info("变更收敛：%d 条旧版本知识标记 archived", len(stale))
    return len(stale)


async def ingest_document(
    runtime: Any,
    project_root: Path,
    project_id: str,
    path: Path,
    dedup: DedupSet,
    *,
    status: str = "pending_review",
    tags: Optional[List[str]] = None,
    allow_sensitive: bool = False,
    text: Optional[str] = None,
    require_run_tag_to_revive: bool = False,
) -> Dict[str, Any]:
    """单文档采集（bootstrap 全量 / watch 增量共用）：切片 → 校验 → 同 source 三分支
    （命中 active/pending 保留不动；命中 archived 复活；未命中去重后写入）→
    旧版本切片收敛 archived。

    复活统一回到入参 status（bootstrap 车道 A=active，B/C=pending_review；
    watch=active）——当前生效版本应反映「现在文件里有的」。
    """
    stats: Dict[str, Any] = {
        "written": 0, "skipped": 0, "duplicates": 0,
        "superseded": 0, "revived": 0, "title": None,
    }
    if text is None:
        text = read_text_tolerant(path)
    if text is None:
        stats["skipped"] = 1
        return stats
    from ..core.models import KnowledgeItem

    root = Path(project_root)
    rel = str(path.relative_to(root))
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id, content, status, tags FROM knowledge_items "
        "WHERE project_id = ? AND source_url = ?",
        (project_id, rel),
    ) as cur:
        source_rows = await cur.fetchall()
    by_hash: Dict[str, dict] = {}
    for row in source_rows:
        by_hash.setdefault(content_hash(row["content"]), row)

    kept_hashes: Set[str] = set()
    now = datetime.now(timezone.utc).isoformat()
    for chunk in slice_document(path, **chunk_kwargs_from_config(getattr(runtime, "config", None))):
        if stats["title"] is None:
            stats["title"] = chunk.title
        result = validate_chunk(
            chunk.content,
            allow_sensitive=allow_sensitive,
            min_tokens=0 if chunk.kind == "index" else MIN_TOKENS,
        )
        if not result.ok:
            stats["skipped"] += 1
            continue
        kept_hashes.add(content_hash(result.content))
        existing = by_hash.get(content_hash(result.content))
        if existing is not None:
            if existing["status"] in ("active", "pending_review"):
                continue  # 未变章节：保留原条目（id/状态/排序不动）
            if existing["status"] == "archived":
                if require_run_tag_to_revive and "bootstrap_run_id:" not in (existing["tags"] or ""):
                    continue
                await conn.execute(
                    "UPDATE knowledge_items SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, existing["id"]),
                )
                stats["revived"] += 1
                continue
            if existing["status"] in _DECIDED_STATUSES:
                continue  # 尊重用户裁决，不复活不重写
        if dedup.is_duplicate(result.content):
            stats["duplicates"] += 1
            continue
        item_tags = list(tags or []) + (["risk"] if result.risk else [])
        if chunk.kind == "index" and "signal:doc-index" not in item_tags:
            item_tags.append("signal:doc-index")
        await runtime.knowledge.add(KnowledgeItem(
            project_id=project_id, title=f"{path.name}# {chunk.title}",
            content=result.content, status=status, content_type="documentation",
            domain="bootstrap", tags=item_tags, source_url=rel,
        ))
        stats["written"] += 1
    await conn.commit()
    stats["superseded"] = await reconcile_scope(
        runtime, project_id, "source_url = ?", [rel], kept_hashes
    )
    return stats


class IncrementalLearner:
    """文件监听学习（§10.9.5 静默学习）：文档变化 → 防抖 → 重切片写入"""

    def __init__(self, runtime: Any, project_root: Path, project_id: str):
        self._runtime = runtime
        self._root = project_root
        self._project_id = project_id
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch_loop(self) -> None:
        from watchfiles import awatch

        logger.info("文件监听学习已启动：%s", self._root)
        async for changes in awatch(
            self._root, stop_event=self._stop, debounce=int(_DEBOUNCE_S * 1000),
        ):
            for _, path_str in changes:
                path = Path(path_str)
                if path.suffix.lower() not in _WATCH_EXTS:
                    continue
                if any(part in EXCLUDED_DIRS for part in path.parts):
                    continue
                try:
                    await self._relearn(path)
                except Exception:
                    logger.exception("增量学习失败：%s", path)

    async def _relearn(self, path: Path) -> None:
        """单文件增量重学：复用 ingest_document（同 source 收敛 + 复活），
        watch 静默学习直写 active（§10.9.5）"""
        stats = await ingest_document(
            self._runtime, self._root, self._project_id, path, DedupSet(),
            status="active", tags=["signal:docs", "incremental"],
        )
        if stats["written"] or stats["superseded"] or stats["revived"]:
            logger.info(
                "增量学习 %s：写入 %d，收敛 %d，复活 %d",
                path.relative_to(self._root), stats["written"], stats["superseded"], stats["revived"],
            )
