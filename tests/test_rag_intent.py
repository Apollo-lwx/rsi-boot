from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search
from rsi_boot.rag.query import expand_query


def test_paraphrase_hits_without_exact_title():
    docs = [MemoryDoc(
        id="b"*32, type="convention",
        title="禁止 SELECT *", content="查询必须显式列出列名，禁止星号",
    )]
    idx = build_index(docs)
    expanded, intent, _ = expand_query("别把所有列一次查出来")
    assert expanded != "禁止 SELECT *"
    hits = search(idx, expanded, top_n=5)
    assert hits and hits[0][0] == "b"*32


def test_english_paraphrase_hits_chinese_doc():
    docs = [MemoryDoc(id="c"*32, type="convention",
                      title="禁止 SELECT *", content="必须写列名")]
    idx = build_index(docs)
    expanded, _, _ = expand_query("don't select star from the table")
    hits = search(idx, expanded, top_n=5)
    assert any(doc_id == "c"*32 for doc_id, _ in hits)
