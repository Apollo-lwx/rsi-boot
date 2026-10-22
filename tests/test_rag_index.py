from pathlib import Path

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search, tokenize


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
