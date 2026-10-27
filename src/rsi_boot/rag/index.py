"""Chunked inverted index: CJK bigram tokenize, BM25 on body and title separately.

`scorer="tfidf"` keeps the previous IDF × tf product so rank comparisons can
measure the BM25 change on the same postings.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.chunker import chunk_text

# Pulled from knowledge/retriever.py — in-memory invert, not SQLite FTS.
_CJK_RUN = re.compile(r"[一-鿿　-〿＀-￯]+")
_ASCII_SPLIT = re.compile(r"[^0-9A-Za-z_\-\s]+")
_INTENT_TOKEN = re.compile(r"intent:[0-9A-Za-z_-]+")
_ASCII_TERM = re.compile(r"^[a-z0-9][a-z0-9_-]{2,}$")
_HAN_TERM = re.compile(r"[\u4e00-\u9fff]")
_DEFAULT_MIN_SCORE_RATIO = 0.25
# Below this, a query term is a common bigram (IDF already faded). A prohibition
# that shares only those terms is not specific enough to pin.
_RARE_IDF = 0.5
_BM25_K1 = 1.2
# Memories are a sentence or two. b=0.75 is for long web pages and treats a
# normal note as long once the corpus is full of one-liners.
_BM25_B = 0.3
# Title is its own field (own length, own average). It is not copied into the
# body postings, and it is not clipped by the body score.
_TITLE_WEIGHT = 2.0
# Synonyms appended by expand_query. Words the user typed stay at 1.
_EXPANSION_WEIGHT = 0.3
_DERIVED_INDEX_KEYS = ("idf", "chunks", "avgdl", "avg_title_len", "title_len")


def tokenize(text: str) -> list[str]:
    """CJK 连续段切 bigram（单字保留）。

    其余标点改成空白再按空白取词，词内的 - 与 _ 保留，统一小写。
    这样 sunrise?、Melanie:、foo() 能和正文里的 sunrise、melanie、foo 对上。
    intent:explain 整段保留，避免意图名变成裸词去撞 EXPLAIN 文档。
    """
    terms: list[str] = []
    for run in _CJK_RUN.finditer(text):
        chunk = run.group(0)
        if len(chunk) == 1:
            terms.append(chunk)
        else:
            terms.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    held: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        held.append(match.group(0).lower())
        return f" intenthold{len(held) - 1} "

    remainder = _INTENT_TOKEN.sub(_hold, _CJK_RUN.sub(" ", text))
    remainder = _ASCII_SPLIT.sub(" ", remainder)
    for word in remainder.split():
        peeled = word.strip("-")
        if not peeled:
            continue
        lower = peeled.lower()
        if lower.startswith("intenthold") and lower[10:].isdigit():
            terms.append(held[int(lower[10:])])
            continue
        terms.append(lower)
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
    """Body + payload. Title is a separate BM25 field and is not repeated here."""
    parts = [doc.content or ""]
    if doc.payload:
        parts.append(_flatten_payload(doc.payload))
    return "\n\n".join(p for p in parts if p)


def _idf(n: int, df: int) -> float:
    """Lucene BM25 IDF once a term is actually common.

    Shared by two notes, 列名 is the topic, so it keeps a positive weight.
    A bigram in a dozen memories (不要) fades toward 0 instead of a floor of 1.
    """
    lucene = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
    if df >= 12:
        return lucene
    classic = math.log((n + 1) / (df + 0.5)) + 1.0
    if df <= 4:
        return classic
    weight = (df - 4) / 8.0
    return (1.0 - weight) * classic + weight * lucene


def ensure_idf(index: dict) -> dict:
    """惰性补齐 idf：落盘快照会省略可由 postings+n 反推的 idf，读取时在此重算并缓存。"""
    if index.get("idf") is None:
        postings = index.get("postings") or {}
        n = int(index.get("n") or 0)
        index["idf"] = {
            term: _idf(n, len(df))
            for term, df in postings.items()
        }
    return index


def serialize_index(index: dict) -> str:
    """落盘快照：省略可由 postings / doc_len / title_tf 反推的派生字段。
    读回时 search 通过 ensure_idf / _prepare_bm25 补齐，检索结果不变。"""
    slim = {k: v for k, v in index.items() if k not in _DERIVED_INDEX_KEYS}
    return json.dumps(slim, ensure_ascii=False)


def _bm25_term(tf: int, idf: float, doc_len: int, avgdl: float) -> float:
    """BM25 term weight. idf is the index's existing smoothed weight (always ≥ 1)."""
    if tf <= 0:
        return 0.0
    if avgdl <= 0:
        norm = 1.0
    else:
        norm = (1.0 - _BM25_B) + _BM25_B * (doc_len / avgdl)
    return idf * (tf * (_BM25_K1 + 1.0)) / (tf + _BM25_K1 * norm)


def _prepare_bm25(index: dict) -> dict:
    """Fill doc_len / title lengths. Old snapshots without doc_len rebuild it from postings."""
    if not index.get("doc_len"):
        lengths: dict[str, int] = defaultdict(int)
        for df in (index.get("postings") or {}).values():
            for doc_id, tf in df.items():
                lengths[str(doc_id)] += int(tf)
        index["doc_len"] = dict(lengths)
    lengths = index["doc_len"]
    index["avgdl"] = (sum(lengths.values()) / len(lengths)) if lengths else 0.0
    title_tf = index.get("title_tf") or {}
    index["title_tf"] = title_tf
    title_len = {
        doc_id: sum(int(v) for v in tfs.values())
        for doc_id, tfs in title_tf.items()
        if tfs
    }
    index["title_len"] = title_len
    index["avg_title_len"] = (sum(title_len.values()) / len(title_len)) if title_len else 0.0
    return index


def index_stamp(docs: list[MemoryDoc]) -> str:
    """Fingerprint of the fields the inverted index reads. Same stamp ⇒ same postings."""
    import hashlib

    digest = hashlib.sha256()
    for doc in sorted(docs, key=lambda item: (item.id, item.type)):
        digest.update(doc.id.encode())
        digest.update(b"\0")
        digest.update((doc.type or "").encode())
        digest.update(b"\0")
        digest.update((doc.title or "").encode())
        digest.update(b"\0")
        digest.update((doc.content or "").encode())
        digest.update(b"\0")
        digest.update(
            json.dumps(doc.payload or {}, ensure_ascii=False, sort_keys=True, default=str).encode()
        )
        digest.update(b"\0")
    digest.update(str(len(docs)).encode())
    return digest.hexdigest()


def build_index(docs: list[MemoryDoc]) -> dict:
    """Invert chunked title+content+payload. Search keys stay document ids."""
    postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    doc_type: dict[str, str] = {}
    doc_len: dict[str, int] = {}
    title_tf: dict[str, dict[str, int]] = {}
    chunks: list[dict] = []
    for doc in docs:
        doc_type[doc.id] = doc.type
        counted: dict[str, int] = defaultdict(int)
        for term in tokenize(doc.title or ""):
            counted[term] += 1
        if counted:
            title_tf[doc.id] = dict(counted)
        pieces = chunk_text(index_text(doc))
        length = 0
        if not pieces:
            doc_len[doc.id] = 0
            continue
        for piece in pieces:
            bag = f"{piece.heading}\n{piece.text}" if piece.heading else piece.text
            tf: dict[str, int] = defaultdict(int)
            for term in tokenize(bag):
                postings[term][doc.id] += 1
                tf[term] += 1
                length += 1
            chunks.append({"doc_id": doc.id, "heading": piece.heading, "tf": dict(tf)})
        doc_len[doc.id] = length
    n = len(docs)
    idf = {term: _idf(n, len(df)) for term, df in postings.items()}
    index = {
        "postings": {term: dict(df) for term, df in postings.items()},
        "idf": idf,
        "doc_type": doc_type,
        "doc_len": doc_len,
        "title_tf": title_tf,
        "n": n,
        "chunks": chunks,
    }
    return _prepare_bm25(index)


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


def rare_query_terms(index: dict, query: str, *, min_idf: float = _RARE_IDF) -> set[str]:
    """Query terms that are specific in this corpus. Empty when every term is common."""
    ensure_idf(index)
    idf: dict[str, float] = index.get("idf") or {}
    rare: set[str] = set()
    for term in tokenize(query):
        weight = idf.get(term)
        if weight is not None and weight >= min_idf:
            rare.add(term)
    return rare


def doc_hits_rare_term(index: dict, doc_id: str, terms: set[str]) -> bool:
    """True when there is no rare term to require, or the doc has one in the body or title."""
    if not terms:
        return True
    if _doc_has_any_term(index, doc_id, terms):
        return True
    title = (index.get("title_tf") or {}).get(doc_id) or {}
    return any(term in title for term in terms)


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


def _query_tf(
    query: str, *, expansion_of: str | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """返回 (排序权重, 原词频)。同义词在排序里变轻，词频本身留给通道门槛。"""
    original = set(tokenize(expansion_of)) if expansion_of is not None else None
    weighted: dict[str, float] = defaultdict(float)
    plain: dict[str, float] = defaultdict(float)
    for term in tokenize(query):
        plain[term] += 1.0
        if original is not None and term not in original:
            weighted[term] += _EXPANSION_WEIGHT
        else:
            weighted[term] += 1.0
    return weighted, plain


def _score_tfidf(
    index: dict, qtf: dict[str, float], types: set[str] | None,
) -> dict[str, float]:
    """Previous ranker: IDF × query tf × document tf, no length normalization."""
    scores: dict[str, float] = {}
    postings: dict[str, dict[str, int]] = index["postings"]
    idf: dict[str, float] = index["idf"]
    doc_type: dict[str, str] = index["doc_type"]
    for term, q_count in qtf.items():
        df = postings.get(term)
        if not df:
            continue
        weight = idf.get(term, 1.0) * q_count
        for doc_id, tf in df.items():
            if types is not None and doc_type.get(doc_id) not in types:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + weight * tf
    return scores


def _score_bm25(
    index: dict,
    qtf: dict[str, float],
    types: set[str] | None,
    q_plain: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    _prepare_bm25(index)
    body: dict[str, float] = {}
    weighted_body: dict[str, float] = {}
    title: dict[str, float] = {}
    postings: dict[str, dict[str, int]] = index["postings"]
    idf: dict[str, float] = index["idf"]
    doc_type: dict[str, str] = index["doc_type"]
    doc_len: dict[str, int] = index["doc_len"]
    avgdl = float(index.get("avgdl") or 0.0)
    title_tf: dict[str, dict[str, int]] = index.get("title_tf") or {}
    title_len: dict[str, int] = index.get("title_len") or {}
    avg_title = float(index.get("avg_title_len") or 0.0)
    for term, q_count in qtf.items():
        term_idf = idf.get(term, 1.0)
        df = postings.get(term)
        if df:
            for doc_id, tf in df.items():
                if types is not None and doc_type.get(doc_id) not in types:
                    continue
                gain = _bm25_term(
                    int(tf), term_idf, int(doc_len.get(doc_id, 0)), avgdl,
                )
                body[doc_id] = body.get(doc_id, 0.0) + q_plain.get(term, q_count) * gain
                weighted_body[doc_id] = weighted_body.get(doc_id, 0.0) + q_count * gain
        for doc_id, tfs in title_tf.items():
            tf = int(tfs.get(term, 0))
            if tf <= 0:
                continue
            if types is not None and doc_type.get(doc_id) not in types:
                continue
            title[doc_id] = title.get(doc_id, 0.0) + _TITLE_WEIGHT * q_count * _bm25_term(
                tf, term_idf, int(title_len.get(doc_id, 0)), avg_title,
            )
    scores: dict[str, float] = {}
    for doc_id in set(weighted_body) | set(title):
        scores[doc_id] = weighted_body.get(doc_id, 0.0) + title.get(doc_id, 0.0)
    return scores, body


def search(
    index: dict,
    query: str,
    *,
    types: set[str] | None = None,
    top_n: int = 5,
    drop_weak: bool = True,
    min_score_ratio: float = _DEFAULT_MIN_SCORE_RATIO,
    scorer: str = "bm25",
    body_of: dict[str, float] | None = None,
    expansion_of: str | None = None,
) -> list[tuple[str, float]]:
    """默认 BM25：正文与标题分字段相加，标题不截断、不重复计入正文。
    `expansion_of` 是用户原话。扩出来的同义词在排序里权重更低；
    `body_of` 仍是未降权的正文分，通道门槛不因此把改写命中滤掉。
    `scorer="tfidf"` 为改前的词频×IDF。
    `body_of` 若给出，写入本次命中的正文分（不含标题），供通道门槛使用。
    实现中不得出现 `doc.title == query`。
    drop_weak 默认打开：强词过滤 + 相对顶分门槛，禁止项检索应传 False。"""
    if scorer not in {"bm25", "tfidf"}:
        raise ValueError(f"unknown scorer: {scorer}")
    ensure_idf(index)
    qtf, q_plain = _query_tf(query, expansion_of=expansion_of)
    if scorer == "tfidf":
        scores = _score_tfidf(index, qtf, types)
        body = scores
    else:
        scores, body = _score_bm25(index, qtf, types, q_plain)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if drop_weak:
        ranked = _drop_weak_hits(
            index, query, ranked, min_score_ratio=min_score_ratio,
        )
    if body_of is not None:
        for doc_id, _score in ranked:
            body_of[doc_id] = float(body.get(doc_id, 0.0))
    return ranked[:top_n]


def best_heading(index: dict, doc_id: str, query: str) -> str:
    """用 BM25 选该文档得分最高的 ## 小节。词频饱和，长小节不再靠堆词压过短小节。"""
    chunks = index.get("chunks") or []
    if not chunks:
        return ""
    ensure_idf(index)
    idf: dict[str, float] = index.get("idf") or {}
    qtf: dict[str, float] = defaultdict(float)
    for term in tokenize(query):
        qtf[term] += 1.0
    lengths = [sum(int(v) for v in (chunk.get("tf") or {}).values()) for chunk in chunks]
    avgdl = sum(lengths) / len(lengths) if lengths else 0.0
    best = -1.0
    heading = ""
    for chunk, length in zip(chunks, lengths):
        if chunk.get("doc_id") != doc_id:
            continue
        score = 0.0
        tfmap = chunk.get("tf") or {}
        for term, q_count in qtf.items():
            tf = int(tfmap.get(term, 0))
            if tf:
                score += q_count * _bm25_term(tf, float(idf.get(term, 1.0)), length, avgdl)
        if score > best:
            best = score
            heading = str(chunk.get("heading") or "")
    return heading
