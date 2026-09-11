"""文档扫描器（§10.9.6）：Markdown/RST/TXT 文档 → 按标题/段落切片。

切片规则：按标题聚合小节；小节 > 4000 token 按段落拆分（§10.9.7 上限）。
向量化在 P2.1 接入，核心层先落文本（embedding BLOB 留空）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .validator import estimate_tokens, read_text_tolerant

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_MAX_CHUNK_TOKENS = 4000


@dataclass
class DocChunk:
    source: Path
    title: str
    content: str


def slice_document(path: Path) -> List[DocChunk]:
    """按标题切片；超长小节按段落二次拆分"""
    text = read_text_tolerant(path)
    if not text:
        return []

    sections: List[tuple[str, str]] = []
    matches = list(_HEADING.finditer(text))
    if not matches:
        sections.append((path.stem, text))
    else:
        if matches[0].start() > 0 and text[: matches[0].start()].strip():
            sections.append((path.stem, text[: matches[0].start()]))
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append((match.group(2).strip(), text[match.start():end]))

    chunks: List[DocChunk] = []
    for title, body in sections:
        if estimate_tokens(body) <= _MAX_CHUNK_TOKENS:
            chunks.append(DocChunk(source=path, title=title, content=body.strip()))
            continue
        # 超长小节按段落拆分，尽量保持语义边界
        buffer = ""
        for para in re.split(r"\n\s*\n", body):
            if estimate_tokens(buffer + para) > _MAX_CHUNK_TOKENS and buffer:
                chunks.append(DocChunk(source=path, title=title, content=buffer.strip()))
                buffer = para
            else:
                buffer = f"{buffer}\n\n{para}" if buffer else para
        if buffer.strip():
            chunks.append(DocChunk(source=path, title=title, content=buffer.strip()))
    return chunks
