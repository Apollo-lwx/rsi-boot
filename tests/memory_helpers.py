"""File-memory stand-ins for tests that used to SELECT sqlite knowledge/conflicts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml

from rsi_boot.memory.paths import review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc


def memory_item_rows(root: Path) -> list[dict]:
    store = MemoryStore(Path(root) / ".rsi")
    rows = []
    for doc in store.list_all():
        extra = doc.extra or {}
        rows.append({
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "status": doc.status,
            "content_type": doc.type,
            "tags": json.dumps(list(doc.tags), ensure_ascii=False),
            "source_url": extra.get("source_url") or doc.source or "",
            "project_id": "local",
            "domain": doc.domain,
        })
    return rows


def memory_conflict_rows(root: Path) -> list[dict]:
    path = Path(root) / ".rsi" / "state" / "conflicts.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        rows = [r for r in (data.get("conflicts") or []) if isinstance(r, dict)]
    else:
        rows = []
    for row in rows:
        if "conflict_type" not in row and "type" in row:
            row["conflict_type"] = row["type"]
        if "user_rule_excerpt" not in row and "excerpt" in row:
            row["user_rule_excerpt"] = row["excerpt"]
    return rows


async def memory_rows_from_sql(root: Path, sql: str, params: tuple = ()) -> list[dict]:
    sql_l = " ".join(sql.lower().split())
    if "rule_conflicts" in sql_l:
        rows = memory_conflict_rows(root)
        if "conflict_type" in sql_l:
            want = next((p for p in params if p in ("version", "incoherent", "doc_code")), None)
            if want is None and "conflict_type = 'version'" in sql_l:
                want = "version"
            if want:
                rows = [r for r in rows if (r.get("conflict_type") or r.get("type")) == want]
        if "status = 'open'" in sql_l:
            rows = [r for r in rows if r.get("status", "open") == "open"]
        return rows
    rows = memory_item_rows(root)
    if "source_url = 'auto-extract'" in sql_l:
        rows = [r for r in rows if r.get("source_url") == "auto-extract"]
    if "status = 'archived'" in sql_l:
        rows = [r for r in rows if r.get("status") == "archived"]
    if "status = 'pending_review'" in sql_l:
        rows = [r for r in rows if r.get("status") == "pending_review"]
    if "tags like" in sql_l:
        for raw in params:
            if isinstance(raw, str) and "%" in raw:
                needle = raw.replace("%", "")
                rows = [r for r in rows if needle in (r.get("tags") or "")]
    if "id in (" in sql_l or " id =" in f" {sql_l}":
        ids = {p for p in params if isinstance(p, str) and len(p) == 32}
        if ids:
            rows = [r for r in rows if r["id"] in ids]
    return rows


def write_memory_item(
    store: MemoryStore,
    *,
    title: str,
    source_url: str = "",
    tags: list[str] | None = None,
    type: str = "convention",
    status: str = "pending_review",
    content: str | None = None,
) -> str:
    item_id = uuid.uuid4().hex
    doc = MemoryDoc(
        id=item_id,
        type=type,
        title=title[:120],
        content=(content or f"{title} 内容足够长 " * 4)[:20000],
        tags=list(tags or []),
        extra={"source_url": source_url} if source_url else {},
    )
    if status == "pending_review":
        dest = review_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    else:
        from rsi_boot.memory.paths import official_dir

        dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(doc, dest=dest)
    return item_id


def write_memory_conflict(
    store: MemoryStore,
    *,
    item_id: str,
    peer_source: str,
    conflict_type: str = "incoherent",
    resolution_note: str | None = "recommended:keep_item",
    excerpt: str = "excerpt",
) -> str:
    path = store.rsi_dir / "state" / "conflicts.yaml"
    rows: list[dict] = []
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [r for r in (data.get("conflicts") or []) if isinstance(r, dict)]
    cid = uuid.uuid4().hex
    rows.append({
        "id": cid,
        "item_id": item_id,
        "user_rule_path": peer_source,
        "type": conflict_type,
        "conflict_type": conflict_type,
        "status": "open",
        "resolution_note": resolution_note,
        "excerpt": excerpt,
        "user_rule_excerpt": excerpt,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"conflicts": rows}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cid


def archive_memory_docs(root: Path, item_ids: list[str], clear_tags: bool = False) -> None:
    store = MemoryStore(Path(root) / ".rsi")
    dest = store.rsi_dir / "memory" / "archive"
    for item_id in item_ids:
        doc = store.read(item_id)
        if clear_tags:
            store.write(doc.model_copy(update={"tags": []}), dest=dest / f"{item_id}.yaml")
            src = store.rsi_dir / doc.path if doc.path else None
            if src is not None and src.exists():
                src.unlink()
        else:
            store.move(item_id, dest)
