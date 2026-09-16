"""catalog.yaml edges and mermaid text. cites come from MemoryDoc.refs only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.memory.store import MemoryStore

ALLOWED_REL = frozenset({"supersedes", "conflicts", "cites", "distilled_from", "implements"})
CATALOG_REL = "catalog.yaml"
_LEGACY_CATALOG_REL = "state/catalog.yaml"


def catalog_path(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / CATALOG_REL


def load_catalog(rsi_dir: Path) -> dict[str, Any]:
    path = catalog_path(rsi_dir)
    if not path.is_file():
        path = Path(rsi_dir) / _LEGACY_CATALOG_REL
    if not path.is_file():
        return {"edges": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"edges": []}
    edges = raw.get("edges") or []
    if not isinstance(edges, list):
        edges = []
    return {"edges": edges}


def save_catalog(rsi_dir: Path, catalog: dict[str, Any]) -> None:
    dest = catalog_path(rsi_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False)
    tmp = Path(str(dest) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def prune_catalog(rsi_dir: Path, store: MemoryStore) -> None:
    """Drop edges whose endpoints are no longer on disk."""
    known = store.list_ids()
    catalog = load_catalog(rsi_dir)
    kept = [
        edge for edge in catalog.get("edges") or []
        if isinstance(edge, dict) and edge.get("from") in known and edge.get("to") in known
    ]
    if len(kept) != len(catalog.get("edges") or []):
        save_catalog(rsi_dir, {"edges": kept})


def add_edge(
    rsi_dir: Path,
    from_id: str,
    to_id: str,
    rel: str,
    extra: dict[str, Any] | None = None,
) -> None:
    if rel not in ALLOWED_REL:
        raise ValueError(f"unknown rel: {rel}")
    catalog = load_catalog(rsi_dir)
    for existing in catalog["edges"]:
        if (
            existing.get("from") == from_id
            and existing.get("to") == to_id
            and existing.get("rel") == rel
        ):
            return
    edge: dict[str, Any] = {"from": from_id, "to": to_id, "rel": rel}
    if extra:
        edge["extra"] = extra
    catalog["edges"].append(edge)
    save_catalog(rsi_dir, catalog)


def cites_from_store(store: MemoryStore) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for doc in store.list_all():
        for ref in doc.refs or []:
            if ref:
                edges.append({"from": doc.id, "to": str(ref), "rel": "cites"})
    return edges


def render_mermaid(rsi_dir: Path, store: MemoryStore | None = None) -> str:
    edges = list(load_catalog(rsi_dir).get("edges") or [])
    if store is not None:
        edges.extend(cites_from_store(store))
    lines = ["graph LR"]
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = str(edge.get("rel") or "")
        if rel not in ALLOWED_REL:
            continue
        frm = str(edge.get("from") or "")
        to = str(edge.get("to") or "")
        if not frm or not to:
            continue
        key = (frm, to, rel)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'  "{frm}" -->|{rel}| "{to}"')
    return "\n".join(lines) + "\n"
