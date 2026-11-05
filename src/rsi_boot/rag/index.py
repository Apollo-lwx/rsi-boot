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
from rsi_boot.rag.temporal import (
    date_match_score,
    extract_iso_dates,
    inline_date_count,
)

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
# 引用块（显式标记的他处拷贝行，如写入侧上下文增强的 "context: ..."）：
# 词项照常被索引，但查询期可按 quote_weight 折权——时间题里引文的日期
# 属于原事件，引用它的记忆是"假朋友"，原事件所在记忆应排在前面。
_QUOTE_LINE_RE = re.compile(
    r"^\s*(?:>|context\s*[:：]|quote\s*[:：]|引用\s*[:：]|上下文\s*[:：])",
    re.IGNORECASE,
)
TEMPORAL_QUOTE_WEIGHT = 0.15
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
        digest.update((doc.created_at or "").encode())
        digest.update(b"\0")
        digest.update((doc.updated_at or "").encode())
        digest.update(b"\0")
    digest.update(str(len(docs)).encode())
    return digest.hexdigest()


def _doc_dates_for(doc: MemoryDoc) -> list[str]:
    """记忆的时间锚点：显式 created_at 优先，其次标题/正文里的日期表达。"""
    dates: list[str] = []
    for stamp in (doc.created_at, doc.updated_at):
        if stamp:
            dates.extend(extract_iso_dates(str(stamp), cap=2))
    if len(dates) < 2:
        head = f"{doc.title or ''}\n{(doc.content or '')[:2000]}"
        for iso in extract_iso_dates(head):
            if iso not in dates:
                dates.append(iso)
    return dates[:8]


def build_index(docs: list[MemoryDoc]) -> dict:
    """Invert chunked title+content+payload. Search keys stay document ids.

    正文按行区分"自有行"与"引用行"（_QUOTE_LINE_RE）：两者都进 postings
    （默认行为不变），引用行额外进 quote_postings，供查询期折权。"""
    postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    quote_postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
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
            qtf: dict[str, int] = defaultdict(int)
            for line in bag.split("\n"):
                target = qtf if _QUOTE_LINE_RE.match(line) else tf
                for term in tokenize(line):
                    target[term] += 1
                    length += 1
            for term, count in tf.items():
                postings[term][doc.id] += count
            for term, count in qtf.items():
                postings[term][doc.id] += count
                quote_postings[term][doc.id] += count
                tf[term] += count
            chunks.append({"doc_id": doc.id, "heading": piece.heading, "tf": dict(tf)})
        doc_len[doc.id] = length
    n = len(docs)
    idf = {term: _idf(n, len(df)) for term, df in postings.items()}
    doc_dates = {doc.id: _doc_dates_for(doc) for doc in docs}
    doc_dates = {doc_id: dates for doc_id, dates in doc_dates.items() if dates}
    doc_inline = {
        doc.id: inline_date_count((doc.content or "")[:2000])
        for doc in docs
    }
    doc_inline = {doc_id: n for doc_id, n in doc_inline.items() if n > 0}
    ref_date = max(
        (iso for dates in doc_dates.values() for iso in dates),
        default=None,
    )
    index = {
        "postings": {term: dict(df) for term, df in postings.items()},
        "quote_postings": {term: dict(df) for term, df in quote_postings.items()},
        "idf": idf,
        "doc_type": doc_type,
        "doc_len": doc_len,
        "title_tf": title_tf,
        "n": n,
        "chunks": chunks,
        "doc_dates": doc_dates,
        "doc_inline": doc_inline,
        "ref_date": ref_date,
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


def _doc_hits_term_or_title(index: dict, doc_id: str, terms: set[str]) -> bool:
    """强词命中：正文或标题任一命中即算（标题命中不应被弱命中过滤误杀）。"""
    if _doc_has_any_term(index, doc_id, terms):
        return True
    title = (index.get("title_tf") or {}).get(doc_id) or {}
    return any(term in title for term in terms)


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
                (cjk_terms and _doc_hits_term_or_title(index, doc_id, cjk_terms))
                or (ascii_strong and _doc_hits_term_or_title(index, doc_id, ascii_strong))
            )
        ]
    if not ranked:
        return []
    floor = ranked[0][1] * min_score_ratio
    return [(doc_id, score) for doc_id, score in ranked if score >= floor]


def _query_tf(
    query: str, *, expansion_of: str | None = None, expansion_weight: float | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """返回 (排序权重, 原词频)。同义词在排序里变轻，词频本身留给通道门槛。"""
    weight = _EXPANSION_WEIGHT if expansion_weight is None else expansion_weight
    original = set(tokenize(expansion_of)) if expansion_of is not None else None
    weighted: dict[str, float] = defaultdict(float)
    plain: dict[str, float] = defaultdict(float)
    for term in tokenize(query):
        plain[term] += 1.0
        if original is not None and term not in original:
            weighted[term] += weight
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
    title_weight: float | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    _prepare_bm25(index)
    tw = _TITLE_WEIGHT if title_weight is None else title_weight
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
            title[doc_id] = title.get(doc_id, 0.0) + tw * q_count * _bm25_term(
                tf, term_idf, int(title_len.get(doc_id, 0)), avg_title,
            )
    scores: dict[str, float] = {}
    for doc_id in set(weighted_body) | set(title):
        scores[doc_id] = weighted_body.get(doc_id, 0.0) + title.get(doc_id, 0.0)
    return scores, body


def _apply_quote_discount(
    index: dict,
    qtf: dict[str, float],
    types: set[str] | None,
    scores: dict[str, float],
    body: dict[str, float],
    quote_weight: float,
) -> None:
    """把引用行词项的 BM25 贡献从全权折成 quote_weight。

    postings 里引用行按全权计分（保持默认得分不变），这里按
    (quote_weight − 1) × quote_gain 回补，等价于引用行按 quote_weight 计权。
    """
    quote_postings: dict[str, dict[str, int]] = index.get("quote_postings") or {}
    if not quote_postings:
        return
    _prepare_bm25(index)
    idf: dict[str, float] = index["idf"]
    doc_type: dict[str, str] = index["doc_type"]
    doc_len: dict[str, int] = index["doc_len"]
    avgdl = float(index.get("avgdl") or 0.0)
    factor = quote_weight - 1.0
    for term, q_count in qtf.items():
        df = quote_postings.get(term)
        if not df:
            continue
        term_idf = idf.get(term, 1.0)
        for doc_id, tf in df.items():
            if doc_id not in scores:
                continue
            if types is not None and doc_type.get(doc_id) not in types:
                continue
            gain = _bm25_term(int(tf), term_idf, int(doc_len.get(doc_id, 0)), avgdl)
            delta = factor * q_count * gain
            scores[doc_id] += delta
            if doc_id in body:
                body[doc_id] += delta


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
    title_weight: float | None = None,
    expansion_weight: float | None = None,
    temporal_signal=None,
    temporal_weight: float = 0.5,
    quote_weight: float = 1.0,
) -> list[tuple[str, float]]:
    """默认 BM25：正文与标题分字段相加，标题不截断、不重复计入正文。
    `expansion_of` 是用户原话。扩出来的同义词在排序里权重更低；
    `body_of` 仍是未降权的正文分，通道门槛不因此把改写命中滤掉。
    `scorer="tfidf"` 为改前的词频×IDF。
    `body_of` 若给出，写入本次命中的正文分（不含标题），供通道门槛使用。
    `temporal_signal`（rag.temporal.TemporalSignal）给出时，按日期匹配度对
    得分做 (1 + temporal_weight × m) 乘性提权；信号为空或无日期时不提权。
    `quote_weight` < 1 时，引用行（context:/引用 等显式标记的拷贝行）的
    词项贡献按比例折减——时间题里引用他处证据的记忆不应压过证据原件。
    默认 1.0 时得分与折权引入前完全一致（只对 bm25 生效）。
    实现中不得出现 `doc.title == query`。
    drop_weak 默认打开：强词过滤 + 相对顶分门槛，禁止项检索应传 False。"""
    if scorer not in {"bm25", "tfidf"}:
        raise ValueError(f"unknown scorer: {scorer}")
    ensure_idf(index)
    qtf, q_plain = _query_tf(query, expansion_of=expansion_of,
                             expansion_weight=expansion_weight)
    if scorer == "tfidf":
        scores = _score_tfidf(index, qtf, types)
        body = scores
    else:
        scores, body = _score_bm25(index, qtf, types, q_plain,
                                   title_weight=title_weight)
    if quote_weight != 1.0 and scorer == "bm25":
        _apply_quote_discount(index, qtf, types, scores, body, quote_weight)
    if temporal_signal is not None and getattr(temporal_signal, "is_temporal", False):
        doc_dates: dict[str, list[str]] = index.get("doc_dates") or {}
        doc_inline: dict[str, int] = index.get("doc_inline") or {}
        bare = getattr(temporal_signal, "bare", False)
        for doc_id in list(scores):
            match = date_match_score(doc_dates.get(doc_id) or [], temporal_signal)
            if match == 0.0 and bare:
                # 裸时间题（"when did X?"）：提权内嵌日期密度高的文档，
                # 回答"什么时候"的证据句往往自带第二个时间表达
                match = min(int(doc_inline.get(doc_id, 0)), 2) * 0.3
            if match > 0:
                scores[doc_id] = scores[doc_id] * (1.0 + temporal_weight * match)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if drop_weak:
        ranked = _drop_weak_hits(
            index, query, ranked, min_score_ratio=min_score_ratio,
        )
    if body_of is not None:
        for doc_id, _score in ranked:
            body_of[doc_id] = float(body.get(doc_id, 0.0))
    return ranked[:top_n]


def _doc_terms(index: dict, doc_id: str) -> set[str]:
    """文档词集：标题 + 各 chunk 的 tf 键。供多样性去重算重叠。"""
    terms: set[str] = set((index.get("title_tf") or {}).get(doc_id) or {})
    for chunk in index.get("chunks") or []:
        if chunk.get("doc_id") == doc_id:
            terms.update((chunk.get("tf") or {}).keys())
    return terms


def diversify_ranked(
    index: dict,
    ranked: list[tuple[str, float]],
    *,
    lam: float = 0.75,
) -> list[tuple[str, float]]:
    """MMR 式贪心去重：相关性保持主导（λ=0.75），与已选文档词面重叠高的候选降级。
    多跳题的多条证据往往词面各异，而上下文增强会让相邻 turn 近似重复——
    不去重时 top-k 被同一事件的几个相邻 turn 占满，第二条证据被挤出。"""
    if len(ranked) <= 1:
        return ranked
    best = ranked[0][1] or 1.0
    terms: dict[str, set[str]] = {}

    def _terms(doc_id: str) -> set[str]:
        if doc_id not in terms:
            terms[doc_id] = _doc_terms(index, doc_id)
        return terms[doc_id]

    selected: list[tuple[str, float]] = []
    remaining = list(ranked)
    while remaining:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_idx, best_val = 0, float("-inf")
        for i, (doc_id, score) in enumerate(remaining):
            overlap = 0.0
            mine = _terms(doc_id)
            if mine:
                for sel_id, _ in selected:
                    other = _terms(sel_id)
                    if not other:
                        continue
                    inter = len(mine & other)
                    union = len(mine | other)
                    if union:
                        overlap = max(overlap, inter / union)
            value = lam * (score / best) - (1.0 - lam) * overlap
            if value > best_val:
                best_val, best_idx = value, i
        selected.append(remaining.pop(best_idx))
    return selected


def coverage_rerank(
    index: dict,
    ranked: list[tuple[str, float]],
    query: str,
    *,
    gamma: float = 0.4,
) -> list[tuple[str, float]]:
    """集合覆盖重排：每选中一篇，把它盖住的稀有查询词从"待覆盖"里划掉，
    后续候选按对剩余词的覆盖加分。多跳/开放域题的多条证据通常各覆盖查询的一部分。
    与 MMR 不同：不惩罚重叠，只奖励增量覆盖——实体名重叠不会误伤。"""
    if len(ranked) <= 1:
        return ranked
    ensure_idf(index)
    idf: dict[str, float] = index.get("idf") or {}
    wanted0 = {
        term for term in tokenize(query)
        if term in idf and idf.get(term, 0.0) >= _RARE_IDF
    }
    if not wanted0:
        return ranked
    best = ranked[0][1] or 1.0
    terms: dict[str, set[str]] = {}

    def _terms(doc_id: str) -> set[str]:
        if doc_id not in terms:
            terms[doc_id] = _doc_terms(index, doc_id)
        return terms[doc_id]

    wanted = set(wanted0)
    selected: list[tuple[str, float]] = []
    remaining = list(ranked)
    while remaining:
        if not selected:
            top = remaining.pop(0)
            selected.append(top)
            wanted -= _terms(top[0]) & wanted0
            continue
        best_idx, best_val = 0, float("-inf")
        for i, (doc_id, score) in enumerate(remaining):
            cover = len(_terms(doc_id) & wanted) / len(wanted0)
            value = score / best + gamma * cover
            if value > best_val:
                best_val, best_idx = value, i
        top = remaining.pop(best_idx)
        selected.append(top)
        wanted -= _terms(top[0]) & wanted0
    return selected


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
