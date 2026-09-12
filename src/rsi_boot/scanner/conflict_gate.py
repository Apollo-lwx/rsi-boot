"""Bootstrap 冲突门：对拟写入草稿做版本/文码/极性检测，产出 hold 集合与冲突草稿。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Set

from ..injector.conflict import _PERMISSIVE, _PROHIBITIVE
from .correlation_engine import _jaccard, _words
from .version_conflict import detect_version_families

_BODY_JACCARD_THRESHOLD = 0.85
_DOC_JACCARD_THRESHOLD = 0.5
_EXCERPT_WINDOW = 50
_MIN_KEY_PHRASE_LEN = 4

_CLASS_RE = re.compile(r"\bclass\s+([A-Z]\w+)\b")
_BACKTICK_RE = re.compile(r"`([A-Z]\w+)`")
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-zA-Z]*)+)\b")
_WORD_RE = re.compile(r"[a-zA-Z0-9_\u4e00-\u9fff]+")


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


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _body_jaccard(left: str, right: str) -> float:
    return _jaccard(_words(left), _words(right))


def _norm_src(path: str) -> str:
    return path.replace("\\", "/")


def _key_phrase(title: str) -> str:
    return title.removeprefix("禁止：").removeprefix("禁止:").strip()


def _find_phrase(text: str, phrase: str) -> int:
    if phrase.isascii():
        pattern = re.escape(phrase)
        if phrase[0].isalnum() or phrase[0] == "_":
            pattern = r"\b" + pattern
        if phrase[-1].isalnum() or phrase[-1] == "_":
            pattern += r"\b"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.start() if match else -1
    return text.lower().find(phrase.lower())


def _polarity(window: str) -> str:
    low = window.lower()
    if any(w in low for w in _PROHIBITIVE):
        return "prohibitive"
    if any(w in low for w in _PERMISSIVE):
        return "permissive"
    return "neutral"


def _candidate_phrases(draft: DraftItem) -> List[str]:
    seen: set[str] = set()
    phrases: List[str] = []
    for raw in (_key_phrase(draft.title), draft.title, draft.content):
        for token in _WORD_RE.findall(raw):
            if len(token) >= _MIN_KEY_PHRASE_LEN and token not in seen:
                seen.add(token)
                phrases.append(token)
    return phrases


def _extract_identifiers(content: str) -> Set[str]:
    ids: Set[str] = set()
    for pattern in (_CLASS_RE, _BACKTICK_RE, _CAMEL_RE):
        ids.update(pattern.findall(content))
    return ids


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
) -> None:
    by_source = {_norm_src(d.source_url): d for d in drafts}
    for family in detect_version_families(project_root):
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
) -> None:
    if not skeletons:
        return
    for draft in drafts:
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


def _detect_incoherent_conflicts(
    drafts: list[DraftItem],
    hold_sources: Set[str],
    conflicts: list[ConflictDraft],
) -> None:
    for i in range(len(drafts)):
        for j in range(i + 1, len(drafts)):
            left, right = drafts[i], drafts[j]
            text_l = f"{left.title}\n{left.content}"
            text_r = f"{right.title}\n{right.content}"
            matched = False
            for phrase in _candidate_phrases(left):
                if len(phrase) < _MIN_KEY_PHRASE_LEN:
                    continue
                if _find_phrase(text_r, phrase) < 0:
                    continue
                idx_l = _find_phrase(text_l, phrase)
                idx_r = _find_phrase(text_r, phrase)
                if idx_l < 0 or idx_r < 0:
                    continue
                win_l = text_l[max(0, idx_l - _EXCERPT_WINDOW): idx_l + len(phrase) + _EXCERPT_WINDOW]
                win_r = text_r[max(0, idx_r - _EXCERPT_WINDOW): idx_r + len(phrase) + _EXCERPT_WINDOW]
                pol_l, pol_r = _polarity(win_l), _polarity(win_r)
                if {pol_l, pol_r} != {"prohibitive", "permissive"}:
                    continue
                src_l, src_r = _norm_src(left.source_url), _norm_src(right.source_url)
                hold_sources.add(src_l)
                hold_sources.add(src_r)
                conflicts.append(
                    ConflictDraft(
                        conflict_type="incoherent",
                        left_source=src_l,
                        right_source=src_r,
                        reason=f"共享关键短语「{phrase}」，极性相反（{pol_l} vs {pol_r}）",
                        hold_sources=[src_l, src_r],
                        recommended="coexist",
                        recommended_reason="需用户裁决口径是否可并存",
                    )
                )
                matched = True
                break
            if matched:
                continue


def gate_drafts(
    drafts: list[DraftItem],
    skeletons: list[Any],
    project_root: Path,
) -> GateResult:
    hold_sources: set[str] = set()
    conflicts: list[ConflictDraft] = []
    _detect_version_conflicts(drafts, project_root, hold_sources, conflicts)
    _detect_doc_code_conflicts(drafts, skeletons, hold_sources, conflicts)
    _detect_incoherent_conflicts(drafts, hold_sources, conflicts)
    return GateResult(hold_sources=hold_sources, conflicts=conflicts)
