from pathlib import Path

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import best_heading, build_index, search, tokenize


def test_retriever_source_has_no_exact_title_shortcut():
    src = Path("src/rsi_boot/rag/retriever.py").read_text(encoding="utf-8")
    assert "title ==" not in src and "title==" not in src


def test_tokenize_peels_punctuation_but_keeps_ident_marks():
    assert "sunrise" in tokenize("When did Melanie paint a sunrise?")
    assert "melanie" in tokenize("Melanie: Yeah, I painted that lake sunrise last year!")
    assert "foo" in tokenize("call foo() now")
    assert "user_id" in tokenize("see user_id.")
    assert "awe-inspiring" in tokenize("it felt awe-inspiring")
    assert "caroline" in tokenize("Caroline's friends")
    assert tokenize("记忆") == ["记忆"]
    assert "intent:explain" in tokenize("task intent:explain")
    assert "explain" not in tokenize("task intent:explain")


def test_best_heading_uses_bm25_not_raw_term_frequency():
    """短小节出现一次就够。把同一个词再堆进很长的下一节，不能靠词频抢走标题。"""
    long = ("rediscache " * 2) + " ".join(f"w{i}" for i in range(200))
    doc = MemoryDoc(
        id="a" * 32,
        type="documentation",
        title="手册",
        content=f"## 命中\nrediscache\n\n## 笔记\n{long}",
    )
    index = build_index([doc])
    assert best_heading(index, doc.id, "rediscache") == "命中"


def test_bm25_prefers_short_title_over_repeated_long_note():
    """词频×IDF 会被重复堆词的长文压过；BM25 用长度归一和标题加权把短标题排到前面。"""
    key = "rediscache"
    short = MemoryDoc(id="a" * 32, type="convention", title=key, content=key)
    long = MemoryDoc(
        id="b" * 32,
        type="documentation",
        title="weekly notes",
        content=(key + " ") * 40 + " ".join(f"pad{i}" for i in range(100)),
    )
    index = build_index([short, long])
    tfidf = search(index, key, scorer="tfidf", top_n=2, drop_weak=False)
    bm25 = search(index, key, scorer="bm25", top_n=2, drop_weak=False)
    assert tfidf[0][0] == long.id
    assert bm25[0][0] == short.id


def test_title_hit_beats_body_repetition_when_body_is_off_topic():
    """标题里的词不应被「加分不超过正文一半」截掉。

    正文完全不提这个词、只在标题出现时，应排在「标题无关、正文把词重复若干遍」之前。
    """
    key = "rediscache"
    titled = MemoryDoc(
        id="a" * 32,
        type="convention",
        title=key,
        content=" ".join(f"pad{i}" for i in range(200)),
    )
    repeated = MemoryDoc(
        id="b" * 32,
        type="documentation",
        title="weekly notes",
        content=(key + " ") * 8 + " ".join(f"other{i}" for i in range(40)),
    )
    index = build_index([titled, repeated])
    hits = search(index, key, scorer="bm25", top_n=2, drop_weak=False)
    assert hits[0][0] == titled.id


def _doc_id(n: int) -> str:
    return f"{n:08x}{'d' * 24}"


def test_second_query_term_survives_a_corpus_of_one_liners():
    """语料大多是短句时，b=0.75 会把一条正常记忆当成长文。

    同时含 rediscache 和 ttl 的记忆应排在只含 rediscache 的单词条目前面。
    """
    docs = [
        MemoryDoc(id=_doc_id(10 + i), type="convention", title="pad", content=f"noise{i} token")
        for i in range(6)
    ]
    stub = MemoryDoc(id=_doc_id(1), type="convention", title="misc", content="rediscache")
    good = MemoryDoc(
        id=_doc_id(2),
        type="convention",
        title="policy",
        content="rediscache ttl " + " ".join(f"w{i}" for i in range(24)),
    )
    docs.extend([stub, good])
    hits = search(build_index(docs), "rediscache ttl", scorer="bm25", top_n=2, drop_weak=False)
    assert hits[0][0] == good.id


def test_common_bigram_does_not_outrank_a_rare_term():
    """出现在每条记忆里的「不要」不该压过只出现一次的稀有词。"""
    docs = [
        MemoryDoc(
            id=_doc_id(10 + i),
            type="convention",
            title=f"n{i}",
            content=f"不要改这里 pad{i}",
        )
        for i in range(10)
    ]
    common = MemoryDoc(id=_doc_id(1), type="convention", title="ban", content="不要")
    rare = MemoryDoc(
        id=_doc_id(2),
        type="convention",
        title="cache",
        content="rediscache 不要 " + " ".join(f"w{i}" for i in range(40)),
    )
    docs.extend([common, rare])
    hits = search(build_index(docs), "不要 rediscache", scorer="bm25", top_n=2, drop_weak=False)
    assert hits[0][0] == rare.id


def test_search_finds_turn_when_query_has_trailing_punctuation():
    doc = MemoryDoc(
        id="ab" * 16,
        type="documentation",
        title="lake sunrise",
        content="Melanie: Yeah, I painted that lake sunrise last year!",
    )
    index = build_index([doc])
    hits = search(index, "When did Melanie paint a sunrise?", types={"documentation"}, top_n=5)
    assert hits and hits[0][0] == doc.id
