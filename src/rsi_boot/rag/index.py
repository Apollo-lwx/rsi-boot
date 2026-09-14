"""Chunked inverted index with CJK bigram tokenize and IDF scoring."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.chunker import chunk_text

# Pulled from knowledge/retriever.py — in-memory invert, not SQLite FTS.
_CJK_RUN = re.compile(r"[一-鿿　-〿＀-￯]+")


def tokenize(text: str) -> list[str]:
    """CJK 连续段切 bigram（单字保留），非 CJK 段按空白取词，统一小写。"""
    terms: list[str] = []
    for run in _CJK_RUN.finditer(text):
        chunk = run.group(0)
        if len(chunk) == 1:
            terms.append(chunk)
        else:
            terms.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    for word in _CJK_RUN.sub(" ", text).split():
        terms.append(word.lower())
    return terms


def _flatten_payload(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_payload(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_payload(v) for v in value)
    if value is None:
        return ""
    return str(value)


def index_text(doc: MemoryDoc) -> str:
    parts = [doc.title or "", doc.content or ""]
    if doc.payload:
        parts.append(_flatten_payload(doc.payload))
    return "\n\n".join(p for p in parts if p)


def build_index(docs: list[MemoryDoc]) -> dict:
    """Invert chunked title+content+payload. Search keys stay document ids."""
    postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    doc_type: dict[str, str] = {}
    chunks: list[dict] = []
    for doc in docs:
        doc_type[doc.id] = doc.type
        pieces = chunk_text(index_text(doc))
        if not pieces:
            continue
        for piece in pieces:
            bag = f"{piece.heading}\n{piece.text}" if piece.heading else piece.text
            tf: dict[str, int] = defaultdict(int)
            for term in tokenize(bag):
                postings[term][doc.id] += 1
                tf[term] += 1
            chunks.append({"doc_id": doc.id, "heading": piece.heading, "tf": dict(tf)})
    n = len(docs)
    idf = {
        term: math.log((n + 1) / (len(df) + 1)) + 1.0
        for term, df in postings.items()
    }
    return {
        "postings": {term: dict(df) for term, df in postings.items()},
        "idf": idf,
        "doc_type": doc_type,
        "n": n,
        "chunks": chunks,
    }


def search(
    index: dict,
    query: str,
    *,
    types: set[str] | None = None,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """只按词袋/IDF 打分。实现中不得出现 `doc.title == query`。"""
    scores: dict[str, float] = {}
    postings: dict[str, dict[str, int]] = index["postings"]
    idf: dict[str, float] = index["idf"]
    doc_type: dict[str, str] = index["doc_type"]
    qtf: dict[str, int] = defaultdict(int)
    for term in tokenize(query):
        qtf[term] += 1
    for term, q_count in qtf.items():
        df = postings.get(term)
        if not df:
            continue
        weight = idf.get(term, 1.0) * q_count
        for doc_id, tf in df.items():
            if types is not None and doc_type.get(doc_id) not in types:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + weight * tf
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ranked[:top_n]


def best_heading(index: dict, doc_id: str, query: str) -> str:
    """Heading of the best-scoring ## section chunk for this document."""
    chunks = index.get("chunks") or []
    if not chunks:
        return ""
    idf: dict[str, float] = index.get("idf") or {}
    qtf: dict[str, int] = defaultdict(int)
    for term in tokenize(query):
        qtf[term] += 1
    best = -1.0
    heading = ""
    for chunk in chunks:
        if chunk.get("doc_id") != doc_id:
            continue
        score = 0.0
        tfmap = chunk.get("tf") or {}
        for term, q_count in qtf.items():
            tf = tfmap.get(term, 0)
            if tf:
                score += idf.get(term, 1.0) * q_count * tf
        if score > best:
            best = score
            heading = str(chunk.get("heading") or "")
    return heading
