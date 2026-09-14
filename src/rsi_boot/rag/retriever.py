"""Retrieve by expand_query + inverted search. No exact-title shortcut."""

from __future__ import annotations

from pathlib import Path

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search
from rsi_boot.rag.query import expand_query


def retrieve(
    docs: list[MemoryDoc],
    task: str,
    *,
    role: str | None = None,
    types: set[str] | None = None,
    top_n: int = 5,
    rsi_dir: Path | None = None,
) -> list[tuple[str, float]]:
    expanded, _intent, _confidence = expand_query(task, role=role, rsi_dir=rsi_dir)
    return search(build_index(docs), expanded, types=types, top_n=top_n)
