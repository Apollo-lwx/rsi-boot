"""Reading-pack index and fingerprint IO (host-distill-knowledge Wave A)."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PACKS_DIRNAME = "reading-packs"
MAX_SOURCES_PER_PACK = 40
MAX_PACKS = 80

_TERMINAL = frozenset({"done", "skipped"})


def packs_dir(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "state" / PACKS_DIRNAME


def source_fingerprint(kind: str, path: str = "", hash: str = "", heading: str = "") -> str:
    """sha256 hex of kind + path + hash + heading."""
    payload = f"{kind}{path}{hash}{heading}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pack_fingerprint(sources: list[dict]) -> str:
    """sha256 of sorted member fingerprints."""
    fps = sorted(
        source_fingerprint(
            kind=str(s.get("kind") or ""),
            path=str(s.get("path") or ""),
            hash=str(s.get("hash") or ""),
            heading=str(s.get("heading") or ""),
        )
        for s in sources
    )
    return hashlib.sha256("".join(fps).encode("utf-8")).hexdigest()


def _atomic_write_yaml(dest: Path, payload: dict[str, Any]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
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


def _index_path(rsi_dir: Path) -> Path:
    return packs_dir(rsi_dir) / "index.yaml"


def _pack_path(rsi_dir: Path, pack_id: str) -> Path:
    return packs_dir(rsi_dir) / f"{pack_id}.yaml"


def load_index(rsi_dir: Path) -> dict:
    path = _index_path(rsi_dir)
    if not path.is_file():
        return {"packs": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"packs": []}
    if not isinstance(data.get("packs"), list):
        data["packs"] = []
    return data


def load_pack(rsi_dir: Path, pack_id: str) -> dict | None:
    path = _pack_path(rsi_dir, pack_id)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _old_by_id(rsi_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in load_index(rsi_dir).get("packs") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


def write_packs(
    rsi_dir: Path,
    *,
    bootstrap_run_id: str,
    packs: list[dict],
    force: bool = False,
) -> int:
    old = {} if force else _old_by_id(rsi_dir)
    index_packs: list[dict] = []
    for raw in packs:
        pack_id = str(raw["id"])
        sources = list(raw.get("sources") or [])
        fp = pack_fingerprint(sources)
        status = "pending"
        prev = old.get(pack_id)
        if (
            not force
            and prev
            and prev.get("fingerprint") == fp
            and prev.get("status") in _TERMINAL
        ):
            status = str(prev["status"])
        pack_doc: dict[str, Any] = {
            "id": pack_id,
            "domain": raw.get("domain", ""),
            "title": raw.get("title", ""),
            "status": status,
            "sources": sources,
            "fingerprint": fp,
        }
        _atomic_write_yaml(_pack_path(rsi_dir, pack_id), pack_doc)
        index_packs.append({
            "id": pack_id,
            "domain": pack_doc["domain"],
            "title": pack_doc["title"],
            "status": status,
            "source_count": len(sources),
            "fingerprint": fp,
        })
    _atomic_write_yaml(
        _index_path(rsi_dir),
        {
            "bootstrap_run_id": bootstrap_run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "packs": index_packs,
        },
    )
    return len(packs)


def set_pack_status(
    rsi_dir: Path, pack_id: str, status: str, reason: str = ""
) -> dict:
    pack = load_pack(rsi_dir, pack_id)
    index = load_index(rsi_dir)
    entry = next(
        (
            item
            for item in index.get("packs") or []
            if isinstance(item, dict) and item.get("id") == pack_id
        ),
        None,
    )
    if pack is None or entry is None:
        return {"status": "error", "code": "not_found"}

    if pack.get("status") == status and status in _TERMINAL:
        out: dict[str, Any] = {"status": "success", "id": pack_id, "pack_status": status}
        if reason or pack.get("reason"):
            out["reason"] = reason or pack.get("reason", "")
        return out

    pack["status"] = status
    if reason:
        pack["reason"] = reason
    _atomic_write_yaml(_pack_path(rsi_dir, pack_id), pack)
    entry["status"] = status
    if reason:
        entry["reason"] = reason
    _atomic_write_yaml(_index_path(rsi_dir), index)
    return {"status": "success", "id": pack_id, "pack_status": status}


def unlink_host_judge_queue(rsi_dir: Path) -> None:
    p = Path(rsi_dir) / "host_judge_queue.json"
    if p.is_file():
        p.unlink()
