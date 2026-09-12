"""Bootstrap 冲突门：对拟写入草稿做版本/文码/同桶近似检测，产出 hold 集合与冲突草稿。"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, Sequence, Set

from ..injector.conflict import _PROHIBITIVE
from .correlation_engine import _jaccard, _words
from .version_conflict import detect_version_families

_BODY_JACCARD_THRESHOLD = 0.85
_DOC_JACCARD_THRESHOLD = 0.5
_PEER_JACCARD_MIN = 0.35
# peer 候选带上界复用 _BODY_JACCARD_THRESHOLD（0.85），不另起常量
_MAX_CANDIDATES = 10000

logger = logging.getLogger(__name__)
_GENERIC_IDENTIFIERS = frozenset({
    "MySQL", "InnoDB", "PostgreSQL", "PowerShell", "False", "True", "None",
    "API", "HTTP", "JSON", "HTML", "TODO", "FIXME", "Windows", "Linux",
    "Java", "Python", "TypeError", "ValueError", "ImportError", "ModuleNotFoundError",
    "RuntimeError", "AttributeError", "AssertionError", "Exception",
    "BaseModel", "FullName", "WriteAllText", "StrReplace", "MiniLM",
})

_CLASS_RE = re.compile(r"\bclass\s+([A-Z]\w+)\b")
_BACKTICK_RE = re.compile(r"`([A-Z]\w+)`")
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-zA-Z]*)+)\b")
# peer 分桶/对象抽取用词：CJK 连续段算一个词（correlation_engine._words 仅 ASCII，会把纯中文掏空）
_WORD_RE = re.compile(r"[a-zA-Z0-9_\u4e00-\u9fff]+")

_MODAL_WORDS = (
    "禁止", "不要", "不得", "避免", "严禁", "一律不",
    "允许", "推荐", "优先", "可以",
    "never", "don't", "do not", "must not", "avoid", "always", "prefer",
)


@dataclass
class DraftItem:
    title: str
    content: str
    content_type: str
    source_url: str
    tags: list[str]
    signal: str


@dataclass
class ConflictDraft:
    conflict_type: str
    left_source: str
    right_source: str
    reason: str
    hold_sources: list[str]
    recommended: str
    recommended_reason: str


@dataclass
class GateResult:
    hold_sources: set[str]
    conflicts: list[ConflictDraft]
    omitted_candidates: int = 0


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _gate_words(text: str) -> Set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) > 1}


def _body_jaccard(left: str, right: str) -> float:
    return _jaccard(_words(left), _words(right))


def _peer_sim(left: DraftItem, right: DraftItem) -> float:
    """peer 候选相似度：标题 + 正文的 CJK 感知词集 Jaccard。"""
    return _jaccard(
        _gate_words(f"{left.title}\n{left.content}"),
        _gate_words(f"{right.title}\n{right.content}"),
    )


def _norm_src(path: str) -> str:
    return path.replace("\\", "/")


def _extract_identifiers(content: str) -> Set[str]:
    ids: Set[str] = set()
    for pattern in (_CLASS_RE, _BACKTICK_RE, _CAMEL_RE):
        ids.update(pattern.findall(content))
    return {ident for ident in ids if ident not in _GENERIC_IDENTIFIERS}


def _skeleton_haystack(skeleton: Any) -> str:
    symbols = getattr(skeleton, "symbols", None) or []
    to_text = getattr(skeleton, "to_text", None)
    text = to_text() if callable(to_text) else ""
    return f"{text} {' '.join(symbols)}"


def _identifier_in_skeleton(identifier: str, skeleton: Any) -> bool:
    haystack = _skeleton_haystack(skeleton)
    if identifier in haystack:
        return True
    return any(identifier in sym for sym in getattr(skeleton, "symbols", []) or [])


def _associate_skeleton(draft: DraftItem, skeletons: list[Any]) -> Any | None:
    doc_words = _words(Path(draft.source_url).stem.replace("_", " ").replace("-", " "))
    title_words = _words(draft.title)
    best: tuple[Any, float] | None = None
    for sk in skeletons:
        stem_words = _words(Path(sk.rel_path).stem.replace("_", " ").replace("-", " "))
        if not stem_words:
            continue
        sim_doc = _jaccard(doc_words, stem_words)
        sim_title = _jaccard(title_words, stem_words)
        sim = max(sim_doc, sim_title)
        if sim >= _DOC_JACCARD_THRESHOLD and (best is None or sim > best[1]):
            best = (sk, sim)
    return best[0] if best else None


def _detect_version_conflicts(
    drafts: list[DraftItem],
    project_root: Path,
    hold_sources: Set[str],
    conflicts: list[ConflictDraft],
    include: Optional[Sequence[str]] = None,
) -> None:
    by_source = {_norm_src(d.source_url): d for d in drafts}
    for family in detect_version_families(project_root, include=include):
        left = by_source.get(_norm_src(family.legacy_rel))
        right = by_source.get(_norm_src(family.current_rel))
        if left is None or right is None:
            continue
        if _norm_title(left.title) != _norm_title(right.title):
            continue
        if left.source_url == right.source_url:
            continue
        if _body_jaccard(left.content, right.content) >= _BODY_JACCARD_THRESHOLD:
            continue
        hold_sources.add(_norm_src(left.source_url))
        hold_sources.add(_norm_src(right.source_url))
        conflicts.append(
            ConflictDraft(
                conflict_type="version",
                left_source=_norm_src(left.source_url),
                right_source=_norm_src(right.source_url),
                reason=family.reason,
                hold_sources=[_norm_src(left.source_url), _norm_src(right.source_url)],
                recommended="keep_peer",
                recommended_reason=f"倾向仍有效侧：{family.current_rel}",
            )
        )


def _detect_doc_code_conflicts(
    drafts: list[DraftItem],
    skeletons: list[Any],
    hold_sources: Set[str],
    conflicts: list[ConflictDraft],
    on_progress: Callable[[int], None] | None = None,
) -> None:
    if not skeletons:
        if on_progress is not None and drafts:
            on_progress(len(drafts))
        return
    for i, draft in enumerate(drafts):
        if on_progress is not None:
            on_progress(i + 1)
        if draft.signal != "docs":
            continue
        skeleton = _associate_skeleton(draft, skeletons)
        if skeleton is None:
            continue
        missing = [
            ident
            for ident in _extract_identifiers(draft.content)
            if not _identifier_in_skeleton(ident, skeleton)
        ]
        if not missing:
            continue
        src = _norm_src(draft.source_url)
        hold_sources.add(src)
        conflicts.append(
            ConflictDraft(
                conflict_type="doc_code",
                left_source=src,
                right_source=_norm_src(skeleton.rel_path),
                reason=f"文档点名 {', '.join(sorted(missing))}，骨架中不存在",
                hold_sources=[src],
                recommended="keep_peer",
                recommended_reason="以代码骨架为现状对照",
            )
        )


def _h1_text(draft: DraftItem) -> str | None:
    for line in draft.content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _peer_parent_key(draft: DraftItem) -> str | None:
    """父目录 + 一级标题 桶键；auto-extract / item: / 无路径来源返回 None（只进标题桶）。"""
    src = _norm_src(draft.source_url)
    if not src:
        return None
    parent = PurePosixPath(src).parent
    if str(parent) in ("", "."):
        return None
    return f"{parent}|{_norm_title(_h1_text(draft) or draft.title)}"


def _strip_modals(text: str) -> str:
    """去语气词后取剩余词块（标题 + 首句），作为约束对象近似。"""
    first = re.split(r"[。\n.]", text, maxsplit=1)[0]
    blob = f"{first}"
    for word in _MODAL_WORDS:
        blob = blob.replace(word, " ")
    return " ".join(sorted(_gate_words(blob)))


def _constraint_object(draft: DraftItem) -> str:
    """约束对象近似：自述型文档取正文首个 `# ` 一级标题，否则取标题，去语气词后的排序词集。"""
    return _strip_modals(_h1_text(draft) or draft.title)


def _local_peer_conflict(left: DraftItem, right: DraftItem) -> ConflictDraft | None:
    """本地裁决：对象相同且极性相反 → ConflictDraft(incoherent, hold 两侧)；否则 None。"""
    obj_l = _constraint_object(left)
    obj_r = _constraint_object(right)
    if not obj_l or obj_l != obj_r:
        return None
    pol_l = "prohibitive" if any(w in f"{left.title}{left.content}".lower() for w in _PROHIBITIVE) else "permissive"
    pol_r = "prohibitive" if any(w in f"{right.title}{right.content}".lower() for w in _PROHIBITIVE) else "permissive"
    if pol_l == pol_r:
        return None
    src_l, src_r = _norm_src(left.source_url), _norm_src(right.source_url)
    return ConflictDraft(
        conflict_type="incoherent",
        left_source=src_l,
        right_source=src_r,
        reason=f"约束对象相同，极性相反（{pol_l} vs {pol_r}）",
        hold_sources=[src_l, src_r],
        recommended="coexist",
        recommended_reason="同一约束对象极性相反，需裁决口径",
    )


def _detect_incoherent_conflicts(
    drafts: list[DraftItem],
    hold_sources: Set[str],
    conflicts: list[ConflictDraft],
    peers: list[DraftItem] | None = None,
    on_progress: Callable[[int], None] | None = None,
    progress_offset: int = 0,
    on_match_start: Callable[[int], None] | None = None,
    judge: str = "local",
) -> int:
    by_title: dict[str, list[tuple[DraftItem, bool]]] = defaultdict(list)
    by_parent: dict[str, list[tuple[DraftItem, bool]]] = defaultdict(list)
    items: list[tuple[DraftItem, bool]] = [(d, False) for d in drafts]
    items.extend((p, True) for p in peers or [])
    for i, (item, is_peer) in enumerate(items):
        by_title[_norm_title(item.title)].append((item, is_peer))
        key = _peer_parent_key(item)
        if key is not None:
            by_parent[key].append((item, is_peer))
        if on_progress is not None:
            on_progress(progress_offset + i + 1)
    if on_match_start is not None:
        pair_n = sum(
            len(bucket) * (len(bucket) - 1) // 2
            for bucket in (*by_title.values(), *by_parent.values())
        )
        on_match_start(pair_n)
    candidates: list[tuple[DraftItem, DraftItem, bool, float]] = []
    seen: set[tuple[int, int]] = set()
    done = 0
    for bucket in (*by_title.values(), *by_parent.values()):
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                left, left_peer = bucket[i]
                right, right_peer = bucket[j]
                if left is right or left_peer:
                    continue
                if _norm_src(left.source_url) == _norm_src(right.source_url):
                    continue
                dedupe = (id(left), id(right)) if id(left) < id(right) else (id(right), id(left))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                sim = _peer_sim(left, right)
                done += 1
                if on_progress is not None:
                    on_progress(done)
                if not (_PEER_JACCARD_MIN <= sim < _BODY_JACCARD_THRESHOLD):
                    continue
                candidates.append((left, right, right_peer, sim))
    budget = max(_MAX_CANDIDATES - len(conflicts), 0)
    omitted = 0
    if len(candidates) > budget:
        omitted = len(candidates) - budget
        logger.warning(
            "peer 候选对 %d 超出工作包硬顶 %d，按距 0.85 的相似度距离截断，略去 %d 对",
            len(candidates), _MAX_CANDIDATES, omitted,
        )
        candidates = sorted(candidates, key=lambda c: 0.85 - c[3], reverse=True)[:budget]
    for left, right, right_peer, sim in candidates:
        if judge == "host":
            conflicts.append(
                ConflictDraft(
                    conflict_type="incoherent",
                    left_source=_norm_src(left.source_url),
                    right_source=_norm_src(right.source_url),
                    reason=f"同桶近似条目（Jaccard {sim:.2f}），待宿主裁决",
                    hold_sources=[],
                    recommended="coexist",
                    recommended_reason="待宿主裁决是否互斥，本地不扣留",
                )
            )
            continue
        conflict = _local_peer_conflict(left, right)
        if conflict is None:
            continue
        if right_peer:
            conflict.hold_sources = [_norm_src(left.source_url)]
        hold_sources.update(conflict.hold_sources)
        conflicts.append(conflict)
    return omitted


def gate_drafts(
    drafts: list[DraftItem],
    skeletons: list[Any],
    project_root: Path,
    peers: list[DraftItem] | None = None,
    on_progress: Callable[[int], None] | None = None,
    on_match_start: Callable[[int], None] | None = None,
    include: Optional[Sequence[str]] = None,
    judge: str = "local",
) -> GateResult:
    hold_sources: set[str] = set()
    conflicts: list[ConflictDraft] = []
    _detect_version_conflicts(drafts, project_root, hold_sources, conflicts, include=include)
    _detect_doc_code_conflicts(drafts, skeletons, hold_sources, conflicts, on_progress)
    omitted = _detect_incoherent_conflicts(
        drafts, hold_sources, conflicts, peers,
        on_progress=on_progress, progress_offset=len(drafts),
        on_match_start=on_match_start, judge=judge,
    )
    return GateResult(hold_sources=hold_sources, conflicts=conflicts, omitted_candidates=omitted)
