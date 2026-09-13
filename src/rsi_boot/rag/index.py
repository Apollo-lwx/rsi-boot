"""Whole-doc inverted index with CJK bigram tokenize and IDF scoring."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from rsi_boot.memory.types import MemoryDoc

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


def build_index(docs: list[MemoryDoc]) -> dict:
    """Invert title+content as one bag. P0: no chunker."""
    postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    doc_type: dict[str, str] = {}
    for doc in docs:
        doc_type[doc.id] = doc.type
        for term in tokenize(f"{doc.title}\n{doc.content}"):
            postings[term][doc.id] += 1
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
