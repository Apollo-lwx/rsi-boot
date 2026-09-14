"""Split memory text into paragraphs, optional ## sections, then 800-char pieces."""

from __future__ import annotations

from dataclasses import dataclass

MAX_CHUNK_CHARS = 800


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str = ""


def chunk_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """空行分段；行首 `## ` 为可选小节；超 max_chars 再切。"""
    pieces: list[Chunk] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            pieces.append(Chunk(text=body, heading=heading))
        buf.clear()

    for line in (text or "").splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            continue
        if line.strip() == "":
            flush()
            continue
        buf.append(line)
    flush()

    out: list[Chunk] = []
    for piece in pieces:
        if len(piece.text) <= max_chars:
            out.append(piece)
            continue
        body = piece.text
        for start in range(0, len(body), max_chars):
            out.append(Chunk(text=body[start : start + max_chars], heading=piece.heading))
    return out
