"""Reading-pack index and fingerprint IO (host-distill-knowledge Wave A)."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PACKS_DIRNAME = "reading-packs"
MAX_SOURCES_PER_PACK = 40
MAX_ITEMS_PER_PACK = 20
DENSE_DOMAINS = frozenset({"rules", "skills", "teaching", "gene-map", "patterns"})
LANE_SPECS: tuple[tuple[str, str], ...] = (
    ("skills_rules", "skills / rules"),
    ("teaching", "teaching / gene-map / patterns"),
    ("docs", "docs / README"),
    ("code", "代码"),
    ("git", "git-fix"),
    ("conversation", "对话"),
    ("cursor", ".cursor"),
    ("other", "其余"),
)
LANE_IDS = frozenset(lane_id for lane_id, _ in LANE_SPECS)
_LANE_LABEL = {lane_id: label for lane_id, label in LANE_SPECS}

_TERMINAL = frozenset({"done", "skipped"})
_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
_KIND_PRIORITY = {
    "rules": 0,
    "skills": 1,
    "teaching": 2,
    "gene-map": 3,
    "patterns": 4,
    "README": 20,
    "conversation": 30,
    "git-fix": 31,
}
_CHUNK_DOMAINS = frozenset({"README"})
_HIGH_VALUE_DIRS = (
    ("teaching-cases", "teaching"),
    ("gene-map", "gene-map"),
    ("patterns", "patterns"),
)
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def packs_dir(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "state" / PACKS_DIRNAME


_DOMAIN_DISTILL_TYPE = {
    "skills": "skill",
    "rules": "convention",
    "teaching": "teaching_case",
    "gene-map": "gene_case",
    "patterns": "pattern",
    "docs": "documentation",
    "README": "documentation",
    "conversation": "documentation",
    ".cursor": "documentation",
    "cursor": "documentation",
    "git-fix": "convention",
    "git": "convention",
    "code": "convention",
}


def distill_type_for_domain(domain: str) -> str:
    """阅读包域 → 蒸馏条落盘类型。禁止把 teaching/gene/skill 收成 convention。"""
    name = str(domain or "").strip()
    if name in _DOMAIN_DISTILL_TYPE:
        return _DOMAIN_DISTILL_TYPE[name]
    lane = pack_lane(name)
    if lane == "code" or lane == "git":
        return "convention"
    if lane == "skills_rules":
        return "skill" if name == "skills" else "convention"
    if lane == "teaching":
        return _DOMAIN_DISTILL_TYPE.get(name, "teaching_case")
    return "documentation"


def distill_type_for_pack(pack: dict[str, Any]) -> str:
    """包域优先；源路径落在 gene-map/teaching/patterns 时按高价值域落类型。"""
    for src in pack.get("sources") or []:
        if not isinstance(src, dict):
            continue
        high = _high_value_domain(str(src.get("path") or ""))
        if high:
            return _DOMAIN_DISTILL_TYPE.get(high, "teaching_case")
    return distill_type_for_domain(str(pack.get("domain") or ""))


def pack_lane(domain: str) -> str:
    """Map a pack domain onto a parallel distill lane."""
    name = str(domain or "").strip()
    if name in {"skills", "rules"}:
        return "skills_rules"
    if name in {"teaching", "gene-map", "patterns"}:
        return "teaching"
    if name in {"docs", "README"} or name.startswith("docs/") or name.startswith("docs-"):
        return "docs"
    if name in {"git-fix", "git"}:
        return "git"
    if name == "conversation":
        return "conversation"
    if name in {".cursor", "cursor"}:
        return "cursor"
    if name == "code" or "/" in name:
        return "code"
    return "other"


_AUDIT_SKIP_PREFIXES = (
    ".cursor/audit-result/",
    ".rsi/audit/",
)


def _posix_rel(path: str) -> str:
    posix = str(path or "").replace("\\", "/").strip()
    if posix.startswith("./"):
        posix = posix[2:]
    return posix


def _path_skip_allowed(path: str) -> bool:
    posix = _posix_rel(path)
    if not posix:
        return False
    name = posix.rsplit("/", 1)[-1]
    if name.upper().startswith("README"):
        return True
    lowered = posix.lower()
    return any(
        lowered == prefix.rstrip("/") or lowered.startswith(prefix)
        for prefix in _AUDIT_SKIP_PREFIXES
    )


def skip_allowed(pack: dict[str, Any]) -> bool:
    """仅 README 文件名或 .cursor/audit-result、.rsi/audit 可 skip。产品路径含 ats-/audit 不行。"""
    if str(pack.get("domain") or "") == "README":
        return True
    sources = pack.get("sources") or []
    paths = [str(src.get("path") or "") for src in sources if isinstance(src, dict)]
    paths = [path for path in paths if path]
    if not paths:
        return False
    return all(_path_skip_allowed(path) for path in paths)


def summarize_lanes(packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group packs for parallel host agents. pending_ids are the unfinished ones."""
    buckets: dict[str, dict[str, Any]] = {
        lane_id: {
            "id": lane_id,
            "label": label,
            "total": 0,
            "pending": 0,
            "done": 0,
            "skipped": 0,
            "sources": 0,
            "pending_ids": [],
        }
        for lane_id, label in LANE_SPECS
    }
    for pack in packs:
        if not isinstance(pack, dict) or not pack.get("id"):
            continue
        lane = pack_lane(str(pack.get("domain") or ""))
        bucket = buckets[lane]
        bucket["total"] += 1
        bucket["sources"] += int(pack.get("source_count") or len(pack.get("sources") or []))
        status = str(pack.get("status") or "pending")
        if status == "done":
            bucket["done"] += 1
        elif status == "skipped":
            bucket["skipped"] += 1
        else:
            bucket["pending"] += 1
            bucket["pending_ids"].append(str(pack["id"]))
    return [bucket for bucket in buckets.values() if bucket["total"]]


def summarize_domains(packs: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        domain = str(pack.get("domain") or "") or "(empty)"
        if domain not in counts:
            order.append(domain)
            counts[domain] = {"packs": 0, "sources": 0}
        counts[domain]["packs"] += 1
        counts[domain]["sources"] += int(
            pack.get("source_count") or len(pack.get("sources") or [])
        )
    ranked = sorted(
        order,
        key=lambda domain: (-counts[domain]["packs"], domain),
    )[: max(0, int(limit))]
    return [
        {"domain": domain, "packs": counts[domain]["packs"], "sources": counts[domain]["sources"]}
        for domain in ranked
    ]


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


def _high_value_domain(path: str) -> str | None:
    parts = set(_path_parts(path))
    for marker, domain in _HIGH_VALUE_DIRS:
        if marker in parts:
            return domain
    return None


def _doc_domain(path: str) -> str:
    high = _high_value_domain(path)
    if high:
        return high
    if _is_readme(path):
        return "README"
    parts = _path_parts(path)
    if len(parts) >= 2:
        return parts[0]
    return "docs"


def _conversation_domain(path: str) -> str:
    high = _high_value_domain(path)
    if high:
        return high
    posix = _posix_rel(path).lower()
    if posix.startswith(".cursor/") or posix.startswith(".vscode/") or posix in {".cursor", ".vscode"}:
        return ".cursor"
    return "conversation"


def _code_domain(path: str) -> str:
    parts = _path_parts(path)
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return parts[0]
    return "code"


def _git_file_domain(path: str) -> str:
    high = _high_value_domain(path)
    if high:
        return high
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


def required_item_count(domain: str, source_count: int) -> int:
    n = max(0, int(source_count or 0))
    if n == 0:
        return 0
    if domain in DENSE_DOMAINS:
        return min(n, MAX_ITEMS_PER_PACK)
    return 1


def _split_sources(sources: list[dict], *, domain: str = "") -> list[list[dict]]:
    ordered = sorted(sources, key=_source_sort_key)
    if domain in _CHUNK_DOMAINS:
        return _chunks(ordered, MAX_SOURCES_PER_PACK) if ordered else []
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
        out.extend(_split_sources(groups[key], domain=domain))
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
    """返回 (packs, omitted_source_paths)。

    单包 sources≤40。docs / code / git-fix / teaching 等域全部切完，
    不以总包数截断。同一 path 或同一 hash 只进一个包。
    git_fix 按 files 主目录挂代码/文档包，否则域 git-fix。
    omitted 仅留给无法入包的异常路径，正常采集为空。
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
        domain = _conversation_domain(path)
        kind = "conversation" if domain == "conversation" else "docs"
        add(domain, {"kind": kind, "path": path})

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
        for index, group in enumerate(_split_sources(buckets[domain], domain=domain)):
            candidates.append({
                "id": _pack_id(domain, index),
                "domain": domain,
                "title": _pack_title(domain, group),
                "sources": group,
            })

    return candidates, []


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
    if status == "pending":
        pack.pop("reason", None)
        entry.pop("reason", None)
    elif reason:
        pack["reason"] = reason
    _atomic_write_yaml(_pack_path(rsi_dir, pack_id), pack)
    entry["status"] = status
    if status == "pending":
        entry.pop("reason", None)
    elif reason:
        entry["reason"] = reason
    _atomic_write_yaml(_index_path(rsi_dir), index)
    return {"status": "success", "id": pack_id, "pack_status": status}


def reopen_packs_pending(rsi_dir: Path, pack_ids: list[str]) -> int:
    """把指定包改回 pending，只写一次 index。"""
    wanted = {str(i) for i in pack_ids if i}
    if not wanted:
        return 0
    index = load_index(rsi_dir)
    n = 0
    for entry in index.get("packs") or []:
        if not isinstance(entry, dict) or str(entry.get("id") or "") not in wanted:
            continue
        pack_id = str(entry["id"])
        pack = load_pack(rsi_dir, pack_id)
        if pack is not None:
            pack["status"] = "pending"
            pack.pop("reason", None)
            _atomic_write_yaml(_pack_path(rsi_dir, pack_id), pack)
        entry["status"] = "pending"
        entry.pop("reason", None)
        n += 1
    if n:
        _atomic_write_yaml(_index_path(rsi_dir), index)
    return n


def unlink_host_judge_queue(rsi_dir: Path) -> None:
    p = Path(rsi_dir) / "host_judge_queue.json"
    if p.is_file():
        p.unlink()


def write_relearn_skill(store) -> bool:
    """Upsert official rsi-relearn skill. Write failure returns False."""
    try:
        from ..memory.paths import official_dir
        from ..memory.store import memory_filename
        from ..memory.types import MemoryDoc

        template = Path(__file__).with_name("relearn_skill.md").read_text(encoding="utf-8")
        existing = next(
            (
                doc
                for doc in store.list_official("skill")
                if (doc.payload or {}).get("name") == "rsi-relearn"
            ),
            None,
        )
        doc_id = existing.id if existing else uuid.uuid4().hex
        dest = official_dir(store.rsi_dir, "skill") / memory_filename("rsi-relearn", doc_id)
        if existing and existing.path:
            prior = Path(existing.path)
            dest = prior if prior.is_absolute() else store.rsi_dir / existing.path
        store.write(
            MemoryDoc(
                id=doc_id,
                type="skill",
                title="rsi-relearn",
                content=template,
                status="active",
                description="重新学习时按阅读包蒸馏短知识",
                payload={"name": "rsi-relearn"},
            ),
            dest=dest,
        )
        return True
    except Exception:
        return False
