"""多版本文档家族检测：只发现、不裁决。

两类家族：
1. deprecated：文首第一条 blockquote 自标 DEPRECATED/已废弃，并链接到现行文档；
2. versioned：同目录同基名的 vX.Y 文件并排（v1.0 vs v1.1）。

倾向哪一侧仍有效由调用方结合对话命中计分决定；本模块不改知识状态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence
from urllib.parse import unquote, urlparse

from .signal_discovery import EXCLUDED_DIRS
from .validator import read_text_tolerant

_SELF_DEPRECATED_RE = re.compile(r"^(DEPRECATED|已废弃)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_VERSIONED_NAME_RE = re.compile(
    r"^(?P<base>.+?)[-_]?v(?P<major>\d+)\.(?P<minor>\d+)(?P<rest>.*)$", re.IGNORECASE
)
_HEAD_LINES = 20


@dataclass(frozen=True)
class VersionFamily:
    kind: str            # deprecated | versioned
    legacy_rel: str      # 文件相对路径（posix）
    current_rel: str     # 倾向仍有效的一侧（仅文件信号，可被对话计分翻转）
    reason: str


def is_self_deprecated(text: str) -> bool:
    """仅当「第一条 blockquote」自我宣告废弃时为真。

    现行权威文档常在后文提到「旧版 xxx 已 DEPRECATED」，不得误伤。
    """
    if not text:
        return False
    for raw in text.splitlines()[:_HEAD_LINES]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            inner = line.lstrip(">").strip().lstrip("*").strip()
            return bool(_SELF_DEPRECATED_RE.match(inner))
        # 第一条非空非标题行不是 blockquote → 不是文首自标废弃
        return False
    return False


def _first_md_link(text: str) -> Optional[str]:
    for raw in text.splitlines()[:_HEAD_LINES]:
        match = _MD_LINK_RE.search(raw)
        if match:
            href = match.group(2).strip()
            if href.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path = urlparse(href).path or href
            return unquote(path.split("#", 1)[0])
    return None


def _posix_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_md(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            if name.lower().endswith((".md", ".mdc", ".txt", ".rst", ".adoc")):
                yield Path(dirpath) / name


def detect_version_families(project_root: Path) -> List[VersionFamily]:
    """扫描项目树，返回版本家族（去重后）。两侧文件都必须存在。"""
    root = Path(project_root)
    families: List[VersionFamily] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, legacy: str, current: str, reason: str) -> None:
        if legacy == current:
            return
        key = tuple(sorted((legacy, current)))
        if key in seen:
            return
        seen.add(key)
        families.append(VersionFamily(kind=kind, legacy_rel=legacy, current_rel=current, reason=reason))

    for path in _iter_md(root):
        text = read_text_tolerant(path)
        if not text or not is_self_deprecated(text):
            continue
        href = _first_md_link(text)
        if not href:
            continue
        peer = (path.parent / href).resolve()
        try:
            rel_peer = peer.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if not peer.is_file():
            continue
        _add("deprecated", _posix_rel(path, root), rel_peer, "文首自标废弃并链接到现行文档")

    by_dir_base: dict[tuple[str, str], list[tuple[tuple[int, int], Path]]] = {}
    for path in _iter_md(root):
        match = _VERSIONED_NAME_RE.match(path.name)
        if not match:
            continue
        base = match.group("base").rstrip("-_")
        version = (int(match.group("major")), int(match.group("minor")))
        rel_dir = path.parent.relative_to(root).as_posix()
        by_dir_base.setdefault((rel_dir, base.lower()), []).append((version, path))
    for (_rel_dir, _base), items in by_dir_base.items():
        uniq = {v: p for v, p in items}
        if len(uniq) < 2:
            continue
        ordered = sorted(uniq.items())  # 低版本 → 高版本
        low_ver, low_path = ordered[0]
        high_ver, high_path = ordered[-1]
        _add("versioned", _posix_rel(low_path, root), _posix_rel(high_path, root),
             f"同目录版本并排 v{low_ver[0]}.{low_ver[1]} vs v{high_ver[0]}.{high_ver[1]}（高版本仅作倾向）")
    return families


def score_conversation_hits(rel_path: str, logs: Sequence[Mapping[str, object]]) -> int:
    """文件名（去扩展名）在 raw_input / retrieved_tags 中的提及次数。"""
    stem = Path(rel_path).stem.lower()
    if len(stem) < 4:
        return 0
    hits = 0
    for row in logs:
        blob = str(row.get("raw_input") or "")
        tags = row.get("retrieved_tags") or []
        if isinstance(tags, str):
            blob += " " + tags
        elif isinstance(tags, (list, tuple)):
            blob += " " + " ".join(str(t) for t in tags)
        if stem in blob.lower():
            hits += 1
    return hits


def apply_conversation_hint(
    family: VersionFamily, logs: Sequence[Mapping[str, object]]
) -> VersionFamily:
    """有对话记录时：命中更多的一侧作为倾向现行版。无记录或平手则维持文件信号。"""
    legacy_n = score_conversation_hits(family.legacy_rel, logs)
    current_n = score_conversation_hits(family.current_rel, logs)
    if legacy_n == current_n:
        return family
    if current_n > legacy_n:
        return VersionFamily(
            kind=family.kind, legacy_rel=family.legacy_rel, current_rel=family.current_rel,
            reason=f"{family.reason}；对话更常提及现行侧 {current_n}:{legacy_n}",
        )
    return VersionFamily(
        kind=family.kind, legacy_rel=family.current_rel, current_rel=family.legacy_rel,
        reason=f"{family.reason}；对话更常提及低版本侧 {legacy_n}:{current_n}，倾向已翻转",
    )
