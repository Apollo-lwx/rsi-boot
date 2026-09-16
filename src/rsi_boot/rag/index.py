"""Chunked inverted index with CJK bigram tokenize and IDF scoring."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.chunker import chunk_text

# Pulled from knowledge/retriever.py — in-memory invert, not SQLite FTS.
_CJK_RUN = re.compile(r"[一-鿿　-〿＀-￯]+")
_ASCII_TERM = re.compile(r"^[a-z0-9][a-z0-9_-]{2,}$")
_HAN_TERM = re.compile(r"[\u4e00-\u9fff]")
_DEFAULT_MIN_SCORE_RATIO = 0.25


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


def _ascii_strong_terms(index: dict, query: str) -> set[str]:
    idf: dict[str, float] = index.get("idf") or {}
    weights: dict[str, float] = {}
    for term in tokenize(query):
        if _ASCII_TERM.match(term) and term in idf:
            weights[term] = idf[term]
    if not weights:
        return set()
    ranked = sorted(weights.values())
    cutoff = ranked[len(ranked) // 2] * 0.75
    return {term for term, weight in weights.items() if weight >= cutoff}


def _cjk_query_terms(index: dict, query: str) -> set[str]:
    idf: dict[str, float] = index.get("idf") or {}
    return {
        term for term in tokenize(query)
        if len(term) >= 2 and _HAN_TERM.search(term) is not None and term in idf
    }


def strong_query_terms(index: dict, query: str) -> set[str]:
    """用于弱命中过滤：较稀有的 ASCII 查询词 ∪ 出现在语料中的 CJK 查询词。"""
    return _ascii_strong_terms(index, query) | _cjk_query_terms(index, query)


def _doc_has_any_term(index: dict, doc_id: str, terms: set[str]) -> bool:
    postings: dict[str, dict[str, int]] = index.get("postings") or {}
    return any(doc_id in postings.get(term, {}) for term in terms)


def _drop_weak_hits(
    index: dict,
    query: str,
    ranked: list[tuple[str, float]],
    *,
    min_score_ratio: float,
) -> list[tuple[str, float]]:
    """去掉只靠高频弱词撑起来的命中，也不用弱分硬凑满桶。"""
    if not ranked:
        return []
    ascii_strong = _ascii_strong_terms(index, query)
    cjk_terms = _cjk_query_terms(index, query)
    if ascii_strong or cjk_terms:
        ranked = [
            (doc_id, score)
            for doc_id, score in ranked
            if (
                (cjk_terms and _doc_has_any_term(index, doc_id, cjk_terms))
                or (ascii_strong and _doc_has_any_term(index, doc_id, ascii_strong))
            )
        ]
    if not ranked:
        return []
    floor = ranked[0][1] * min_score_ratio
    return [(doc_id, score) for doc_id, score in ranked if score >= floor]


def search(
    index: dict,
    query: str,
    *,
    types: set[str] | None = None,
    top_n: int = 5,
    drop_weak: bool = True,
    min_score_ratio: float = _DEFAULT_MIN_SCORE_RATIO,
) -> list[tuple[str, float]]:
    """只按词袋/IDF 打分。实现中不得出现 `doc.title == query`。
    drop_weak 默认打开：强词过滤 + 相对顶分门槛，禁止项检索应传 False。"""
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
    if drop_weak:
        ranked = _drop_weak_hits(
            index, query, ranked, min_score_ratio=min_score_ratio,
        )
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
