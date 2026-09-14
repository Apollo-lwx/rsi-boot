"""Reading-pack index and fingerprint IO (host-distill-knowledge Wave A)."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PACKS_DIRNAME = "reading-packs"
MAX_SOURCES_PER_PACK = 40
MAX_PACKS = 80
MAX_OMITTED_PATHS = 200

_TERMINAL = frozenset({"done", "skipped"})
_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
_KIND_PRIORITY = {
    "rules": 0,
    "skills": 1,
    "README": 2,
    "conversation": 8,
    "git-fix": 9,
}
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


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


def _posix_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in _posix_path(path).split("/") if part and part != ".")


def _basename(path: str) -> str:
    parts = _path_parts(path)
    return parts[-1] if parts else ""


def _is_readme(path: str) -> bool:
    return _basename(path).upper().startswith("README")


def _doc_domain(path: str) -> str:
    if _is_readme(path):
        return "README"
    parts = _path_parts(path)
    if len(parts) >= 2:
        return parts[0]
    return "docs"


def _code_domain(path: str) -> str:
    parts = _path_parts(path)
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return parts[0]
    return "code"


def _git_file_domain(path: str) -> str:
    if _is_readme(path):
        return "README"
    name = _basename(path)
    suffix = ""
    if "." in name:
        suffix = "." + name.rsplit(".", 1)[-1].lower()
    if suffix in _DOC_SUFFIXES:
        return _doc_domain(path)
    return _code_domain(path)


def _primary_git_domain(files: list[str]) -> str | None:
    counts: dict[str, int] = {}
    order: list[str] = []
    for raw in files:
        domain = _git_file_domain(raw)
        if domain not in counts:
            order.append(domain)
        counts[domain] = counts.get(domain, 0) + 1
    if not order:
        return None
    return max(order, key=lambda domain: (counts[domain], -order.index(domain)))


def _parent_key(src: dict) -> str:
    path = _posix_path(str(src.get("path") or ""))
    parts = _path_parts(path)
    if len(parts) >= 2:
        return "/".join(parts[:-1])
    return path or str(src.get("hash") or "")


def _source_sort_key(src: dict) -> tuple[str, str, str]:
    return (
        _posix_path(str(src.get("path") or "")),
        str(src.get("hash") or ""),
        str(src.get("kind") or ""),
    )


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _split_sources(sources: list[dict]) -> list[list[dict]]:
    ordered = sorted(sources, key=_source_sort_key)
    if len(ordered) <= MAX_SOURCES_PER_PACK:
        return [ordered]
    groups: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for src in ordered:
        key = _parent_key(src)
        if key not in groups:
            group_order.append(key)
            groups[key] = []
        groups[key].append(src)
    if len(group_order) <= 1:
        return _chunks(ordered, MAX_SOURCES_PER_PACK)
    out: list[list[dict]] = []
    for key in group_order:
        out.extend(_split_sources(groups[key]))
    return out


def _pack_id(domain: str, index: int) -> str:
    slug = _SLUG_RE.sub("-", domain).strip("-").lower() or "pack"
    raw = f"{slug}-{index:04d}"
    if len(raw) < 8:
        raw = f"pack-{slug}-{index:04d}"
    return raw


def _pack_title(domain: str, sources: list[dict]) -> str:
    for src in sources:
        for key in ("heading", "name", "message"):
            value = src.get(key)
            if value:
                return str(value)
    return domain


def _apply_hint(src: dict, version_hints: dict[str, str] | None) -> dict:
    path = _posix_path(str(src.get("path") or ""))
    if version_hints and path in version_hints:
        src = dict(src)
        src["hint"] = "version-family"
    return src


def _take_identity(src: dict, seen_paths: set[str], seen_hashes: set[str]) -> bool:
    path = _posix_path(str(src.get("path") or ""))
    digest = str(src.get("hash") or "")
    if path and path in seen_paths:
        return False
    if digest and digest in seen_hashes:
        return False
    if path:
        seen_paths.add(path)
    if digest:
        seen_hashes.add(digest)
    return True


def _omitted_ident(src: dict) -> str:
    return _posix_path(str(src.get("path") or "")) or str(src.get("hash") or "")


def build_packs(
    *,
    docs: list[dict],
    code: list[dict],
    git_fix: list[dict],
    rules: list[dict],
    skills: list[dict],
    conversations: list[dict],
    version_hints: dict[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """返回 (packs, omitted_source_paths 至多 200 条)。

    单包 sources≤40，总包≤80。同一 path 或同一 hash 只进一个包。
    git_fix 按 files 主目录挂代码/文档包，否则域 git-fix。
    """
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()
    buckets: dict[str, list[dict]] = {}

    def add(domain: str, src: dict) -> None:
        if not _take_identity(src, seen_paths, seen_hashes):
            return
        buckets.setdefault(domain, []).append(_apply_hint(src, version_hints))

    for item in rules:
        path = _posix_path(str(item.get("path") or ""))
        if not path:
            continue
        src: dict[str, Any] = {"kind": "rule", "path": path}
        if item.get("heading"):
            src["heading"] = item["heading"]
        add("rules", src)

    for item in skills:
        path = _posix_path(str(item.get("path") or ""))
        if not path:
            continue
        src = {"kind": "skill", "path": path}
        if item.get("name"):
            src["name"] = item["name"]
        add("skills", src)

    for item in docs:
        path = _posix_path(str(item.get("path") or ""))
        if not path:
            continue
        src = {"kind": "docs", "path": path}
        if item.get("heading"):
            src["heading"] = item["heading"]
        add(_doc_domain(path), src)

    for item in code:
        path = _posix_path(str(item.get("path") or ""))
        if not path:
            continue
        src = {"kind": "code", "path": path}
        if item.get("heading"):
            src["heading"] = item["heading"]
        add(_code_domain(path), src)

    for item in conversations:
        path = _posix_path(str(item.get("path") or ""))
        if not path:
            continue
        add("conversation", {"kind": "conversation", "path": path})

    for item in git_fix:
        digest = str(item.get("hash") or "")
        if not digest:
            continue
        files = [_posix_path(str(f)) for f in (item.get("files") or []) if f]
        src = {"kind": "git_fix", "hash": digest}
        if item.get("message"):
            src["message"] = item["message"]
        src["files"] = files
        primary = _primary_git_domain(files)
        domain = primary if primary and primary in buckets else "git-fix"
        add(domain, src)

    candidates: list[dict] = []
    domains = sorted(
        buckets,
        key=lambda domain: (_KIND_PRIORITY.get(domain, 5), domain),
    )
    for domain in domains:
        for index, group in enumerate(_split_sources(buckets[domain])):
            candidates.append({
                "id": _pack_id(domain, index),
                "domain": domain,
                "title": _pack_title(domain, group),
                "sources": group,
            })

    kept = candidates[:MAX_PACKS]
    omitted: list[str] = []
    seen_omitted: set[str] = set()
    for pack in candidates[MAX_PACKS:]:
        for src in pack["sources"]:
            ident = _omitted_ident(src)
            if not ident or ident in seen_omitted:
                continue
            seen_omitted.add(ident)
            omitted.append(ident)
            if len(omitted) >= MAX_OMITTED_PATHS:
                return kept, omitted
    return kept, omitted


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
