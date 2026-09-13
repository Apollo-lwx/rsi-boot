from pathlib import Path


def test_retriever_source_has_no_exact_title_shortcut():
    src = Path("src/rsi_boot/rag/retriever.py").read_text(encoding="utf-8")
    assert "title ==" not in src and "title==" not in src
