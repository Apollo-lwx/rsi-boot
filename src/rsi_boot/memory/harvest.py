"""Identify leftover bootstrap harvest docs (not host-distilled knowledge)."""

from __future__ import annotations

from pathlib import Path

HARVEST_TITLES = frozenset({
    "代码骨架摘要", "项目配置与规范摘要", "Git 历史分析", "跨信号关联图谱",
})
HARVEST_TAGS = frozenset({
    "signal:docs", "signal:doc-index", "signal:code",
    "signal:config", "signal:git", "signal:correlation",
})

_PEEK_HEAD = 800
_PEEK_TAIL = 800


def is_harvest_doc(doc) -> bool:
    tags = set(doc.tags or [])
    if "signal:distilled" in tags:
        return False
    if (doc.title or "") in HARVEST_TITLES:
        return True
    return bool(tags & HARVEST_TAGS)


def peek_yaml_id(path: Path) -> str | None:
    """Read `id:` from the first lines. Does not parse the YAML body."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index > 8:
                    break
                stripped = line.strip()
                if not stripped.startswith("id:"):
                    continue
                value = stripped[3:].strip().strip("'\"")
                if len(value) == 32 and all(ch in "0123456789abcdef" for ch in value):
                    return value
                return None
    except OSError:
        return None
    return None


def is_harvest_text(text: str) -> bool:
    if "signal:distilled" in text:
        return False
    if any(tag in text for tag in HARVEST_TAGS):
        return True
    return any(title in text for title in HARVEST_TITLES)


def is_harvest_file(path: Path) -> bool:
    """True when head/tail markers look like leftover harvest (no YAML parse)."""
    dest = Path(path)
    try:
        size = dest.stat().st_size
        with dest.open("rb") as fh:
            head = fh.read(_PEEK_HEAD)
            if size > _PEEK_HEAD + _PEEK_TAIL:
                fh.seek(max(0, size - _PEEK_TAIL))
                tail = fh.read(_PEEK_TAIL)
            else:
                tail = b""
    except OSError:
        return False
    return is_harvest_text((head + b"\n" + tail).decode("utf-8", "replace"))


def count_harvest_files(memory_root: Path) -> int:
    root = Path(memory_root)
    if not root.is_dir():
        return 0
    return sum(
        1
        for path in root.rglob("*.yaml")
        if not path.name.endswith(".tmp") and is_harvest_file(path)
    )
