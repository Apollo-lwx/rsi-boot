"""Intent + synonym expansion. Never set expanded text to a document title."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from rsi_boot.preprocessor.intent_classifier import detect_intent
from rsi_boot.ux.lang import preserve_spans

INTENT_SYNONYMS = {
    "星号": ["SELECT", "*", "列名"],
    "所有列": ["SELECT", "*", "列名"],
    "star": ["SELECT", "*", "列名"],
    "*": ["SELECT", "列名"],
    "禁止项": ["prohibition"],
    "must not": ["prohibition"],
}

_SQL_RE = re.compile(
    r"\b(?:SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|JOIN|INTO|VALUES|"
    r"GROUP|ORDER|LIMIT|OFFSET|CREATE|TABLE|INDEX|DROP|ALTER|"
    r"HAVING|UNION|DISTINCT|INNER|OUTER|LEFT|RIGHT|BETWEEN|EXISTS|LIKE)\b",
    re.IGNORECASE,
)
_HAN = re.compile(r"[\u4e00-\u9fff]")


def _load_terms_synonyms(rsi_dir: Path | None = None) -> dict[str, list[str]]:
    path = (
        Path(rsi_dir) / "state" / "terms.yaml"
        if rsi_dir is not None
        else Path.cwd() / ".rsi" / "state" / "terms.yaml"
    )
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("synonyms", data)
    if not isinstance(raw, dict):
        return {}
    extra: dict[str, list[str]] = {}
    for key, val in raw.items():
        if key in ("terms", "words") or not isinstance(val, list):
            continue
        extra[str(key)] = [str(item) for item in val]
    return extra


def _merged_synonyms(rsi_dir: Path | None = None) -> dict[str, list[str]]:
    merged = {key: list(values) for key, values in INTENT_SYNONYMS.items()}
    for key, values in _load_terms_synonyms(rsi_dir).items():
        bucket = merged.setdefault(key, [])
        for item in values:
            if item not in bucket:
                bucket.append(item)
    return merged


def _key_in_text(text: str, key: str) -> bool:
    if key == "*":
        return "*" in text
    if _HAN.search(key):
        return key in text
    return re.search(rf"(?i)\b{re.escape(key)}\b", text) is not None


def _extract_sql(task: str) -> list[str]:
    """SQL tokens preserve_spans would strip; keep them in the expanded bag."""
    remainder = preserve_spans(task)
    found: list[str] = []
    for match in _SQL_RE.finditer(task):
        token = match.group(0)
        if token not in remainder and token not in found:
            found.append(token)
    return found


def expand_query(
    task: str, *, role: str | None = None, rsi_dir: Path | None = None,
) -> tuple[str, str, float]:
    """返回 (expanded_text, intent, confidence)。
    expanded = 原文 + 意图名 + INTENT_SYNONYMS + preserve_spans 抽出的 SQL。
    不得把 expanded 设成某条 title。terms.yaml 只追加，不删内置。"""
    intent, confidence = detect_intent(task, role=role)
    parts = [task, intent]
    seen = {task, intent}
    for key, syns in _merged_synonyms(rsi_dir).items():
        if not _key_in_text(task, key):
            continue
        for syn in syns:
            if syn not in seen:
                parts.append(syn)
                seen.add(syn)
    for span in _extract_sql(task):
        if span not in seen:
            parts.append(span)
            seen.add(span)
    return " ".join(parts), intent, confidence
