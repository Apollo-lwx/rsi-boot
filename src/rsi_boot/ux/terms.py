"""Literal terms that stay in the original language (never translated)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

BUILTIN_TERMS = [
    "SELECT",
    "FROM",
    "WHERE",
    "INSERT",
    "UPDATE",
    "DELETE",
    "JOIN",
    "INTO",
    "VALUES",
    "GROUP",
    "ORDER",
    "LIMIT",
    "OFFSET",
    "CREATE",
    "TABLE",
    "INDEX",
    "DROP",
    "ALTER",
    "HAVING",
    "UNION",
    "DISTINCT",
    "INNER",
    "OUTER",
    "BETWEEN",
    "EXISTS",
    "rsi_recall",
    "rsi_feedback",
    "rsi_learn",
    "rsi_memory",
    "rsi_knowledge_review",
    "rsi_knowledge_add",
    "rsi_knowledge_search",
    "rsi_knowledge_delete",
    "rsi_conflicts",
    "rsi_stats",
    "rsi_review",
    "teach_catch",
    "teach_record",
    "prohibition",
    "prohibitions",
    "pending",
    "feedback_token",
    "alwaysApply",
    "error_signature",
    "content_type",
]


def load_terms_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        data = None
    names: list[str]
    if isinstance(data, list):
        names = [str(item) for item in data]
    elif isinstance(data, dict):
        items = data.get("terms", data.get("words"))
        if isinstance(items, list):
            names = [str(item) for item in items]
        else:
            names = [str(key) for key in data]
    elif isinstance(data, str):
        names = data.splitlines()
    else:
        names = raw.splitlines()
    return [name.strip() for name in names if name and str(name).strip() and not str(name).strip().startswith("#")]


def load_project_terms(root: Path | None = None) -> list[str]:
    base = Path(root) if root is not None else Path.cwd()
    return load_terms_file(base / ".rsi" / "state" / "terms.yaml")


def collect_terms(
    extra_terms: Iterable[str] | None = None,
    root: Path | None = None,
) -> list[str]:
    seen: list[str] = []
    for term in [*BUILTIN_TERMS, *load_project_terms(root), *(extra_terms or [])]:
        text = term.strip()
        if text and text not in seen:
            seen.append(text)
    return seen
