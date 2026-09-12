"""文档扫描器（§10.9.6）：Markdown/RST/TXT 文档 → 自适应切片。

按目标 token 体量切片：碎块合并、超长拆分、许可证/变更日志单条摘要。
向量化在 P2.1 接入，核心层先落文本（embedding BLOB 留空）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .validator import estimate_tokens, read_text_tolerant

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_DIGEST_NAMES = frozenset({"license", "copying"})
_CHANGELOG_RE = re.compile(r"changelog", re.I)
_CHARS_PER_TOKEN = 4


@dataclass
class DocChunk:
    source: Path
    title: str
    content: str
    kind: str = "section"  # section | index | digest


@dataclass
class SliceResult:
    chunks: list[DocChunk] = field(default_factory=list)
    merged_tiny: int = 0
    split_large: int = 0
    skipped_tiny: int = 0
    index_written: int = 0

    def __iter__(self):
        yield from self.chunks


def _is_digest_file(path: Path) -> bool:
    name = path.name.lower()
    if name in _DIGEST_NAMES or name.startswith("license"):
        return True
    return bool(_CHANGELOG_RE.search(name))


def _parse_sections(text: str, stem: str) -> list[tuple[str, str, int]]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(stem, text, 0)]
    sections: list[tuple[str, str, int]] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        sections.append((stem, text[: matches[0].start()], 0))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        level = len(match.group(1))
        sections.append((match.group(2).strip(), text[match.start() : end], level))
    return sections


def _merge_tiny_sections(
    sections: list[tuple[str, str, int]], min_tokens: int
) -> tuple[list[tuple[str, str, int]], int]:
    if not sections:
        return [], 0
    merged: list[tuple[str, str, int]] = []
    merged_tiny = 0
    i = 0
    while i < len(sections):
        title, body, level = sections[i]
        j = i + 1
        while j < len(sections) and sections[j][2] == level and estimate_tokens(body) < min_tokens:
            _, next_body, _ = sections[j]
            body = f"{body}\n\n{next_body}"
            merged_tiny += 1
            j += 1
        merged.append((title, body, level))
        i = j
    return merged, merged_tiny


def _split_text(text: str, *, target_tokens: int, max_tokens: int) -> list[str]:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    target_chars = target_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    buffer = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if estimate_tokens(para) > target_tokens:
            if buffer.strip():
                parts.append(buffer.strip())
                buffer = ""
            start = 0
            while start < len(para):
                end = min(start + target_chars, len(para))
                end = min(end, start + max_chars)
                parts.append(para[start:end])
                start = end
            continue
        candidate = f"{buffer}\n\n{para}" if buffer else para
        cand_tokens = estimate_tokens(candidate)
        if cand_tokens > max_tokens and buffer.strip():
            parts.append(buffer.strip())
            buffer = para
        elif cand_tokens > target_tokens and buffer.strip():
            parts.append(buffer.strip())
            buffer = para
        else:
            buffer = candidate
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def _filter_tiny_chunks(
    chunks: list[DocChunk], min_tokens: int
) -> tuple[list[DocChunk], int]:
    index_chunks = [c for c in chunks if c.kind == "index"]
    content = [c for c in chunks if c.kind != "index"]
    if not content:
        return chunks, 0

    non_tiny = [c for c in content if estimate_tokens(c.content) >= min_tokens]
    if non_tiny:
        return non_tiny + index_chunks, len(content) - len(non_tiny)

    return [content[0]] + index_chunks, len(content) - 1


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def chunk_kwargs_from_config(config: Any) -> dict[str, int]:
    """从 bootstrap.chunk.* 抽出 slice_document 可用的整数参数。"""
    if config is None or not hasattr(config, "get"):
        return {}
    chunk = (config.get("bootstrap") or {}).get("chunk") or {}
    if not isinstance(chunk, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("target_tokens", "min_tokens", "max_tokens"):
        val = chunk.get(key)
        if val is not None:
            out[key] = int(val)
    return out


def slice_document(
    path: Path,
    *,
    target_tokens: int = 400,
    min_tokens: int = 80,
    max_tokens: int = 1500,
) -> SliceResult:
    """自适应切片：碎块合并、超长拆分、许可证/变更日志单条摘要。"""
    text = read_text_tolerant(path)
    if not text:
        return SliceResult()

    result = SliceResult()

    if _is_digest_file(path):
        content = _truncate_to_tokens(text.strip(), max_tokens)
        result.chunks.append(
            DocChunk(source=path, title=path.name, content=content, kind="digest")
        )
        return result

    has_headings = bool(_HEADING.search(text))
    sections = _parse_sections(text, path.stem)
    if has_headings:
        sections, result.merged_tiny = _merge_tiny_sections(sections, min_tokens)

    pending: list[DocChunk] = []
    for title, body, _level in sections:
        body = body.strip()
        if not body:
            continue
        tokens = estimate_tokens(body)
        if tokens <= target_tokens:
            pending.append(
                DocChunk(source=path, title=title, content=body, kind="section")
            )
            continue

        parts = _split_text(body, target_tokens=target_tokens, max_tokens=max_tokens)
        result.split_large += 1
        part_titles = [f"{title} ({i + 1}/{len(parts)})" for i in range(len(parts))]
        for part_title, part in zip(part_titles, parts):
            pending.append(
                DocChunk(source=path, title=part_title, content=part, kind="section")
            )

        if not has_headings and len(parts) >= 3:
            index_lines = "\n".join(f"- {t}" for t in part_titles)
            index_content = f"# {path.stem} 知识目录\n\n{index_lines}"
            pending.append(
                DocChunk(
                    source=path,
                    title=f"{path.stem} 目录",
                    content=index_content,
                    kind="index",
                )
            )
            result.index_written += 1

    result.chunks, skipped = _filter_tiny_chunks(pending, min_tokens)
    result.skipped_tiny += skipped
    if result.index_written == 0:
        section_titles = [c.title for c in result.chunks if c.kind == "section"]
        if len(section_titles) >= 3:
            index_lines = "\n".join(f"- {t}" for t in section_titles)
            result.chunks.append(
                DocChunk(
                    source=path,
                    title=f"{path.stem} 目录",
                    content=f"# {path.stem} 知识目录\n\n{index_lines}",
                    kind="index",
                )
            )
            result.index_written += 1
    return result
