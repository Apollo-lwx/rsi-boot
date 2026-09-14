"""Official and pending directory mapping under .rsi/memory."""

from __future__ import annotations

from pathlib import Path

_OFFICIAL_DIR_NAMES = {
    "prohibition": "prohibitions",
    "convention": "conventions",
    "documentation": "documentation",
    "skill": "skills",
    "episode": "episodes",
    "gene_case": "gene-map",
    "teaching_case": "teaching-cases",
    "pattern": "patterns",
}
_PENDING_TYPES = frozenset({"prohibition", "convention", "skill"})


def official_dir(rsi_dir: Path, type: str) -> Path:
    name = _OFFICIAL_DIR_NAMES.get(type)
    if name is None:
        raise ValueError(f"unknown memory type: {type}")
    return rsi_dir / "memory" / name


def pending_dir(rsi_dir: Path, type: str) -> Path:
    if type not in _PENDING_TYPES:
        raise ValueError(f"pending is only for prohibition/convention/skill, not {type}")
    return rsi_dir / "memory" / "pending" / _OFFICIAL_DIR_NAMES[type]


def review_dir(rsi_dir: Path, type: str) -> Path:
    """pending_review location for any type (bootstrap hold / lane B)."""
    name = _OFFICIAL_DIR_NAMES.get(type)
    if name is None:
        raise ValueError(f"unknown memory type: {type}")
    return rsi_dir / "memory" / "pending" / name
