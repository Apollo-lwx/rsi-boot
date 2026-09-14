def test_blank_line_splits_paragraphs():
    from rsi_boot.rag.chunker import chunk_text

    chunks = chunk_text("第一段内容。\n\n第二段内容。")
    texts = [c.text.strip() for c in chunks]
    assert len(texts) == 2
    assert texts[0] == "第一段内容。"
    assert texts[1] == "第二段内容。"


def test_heading_starts_optional_section():
    from rsi_boot.rag.chunker import chunk_text

    chunks = chunk_text("前言一段。\n\n## 列名规则\n必须写列名，禁止星号。")
    headed = [c for c in chunks if c.heading == "列名规则"]
    assert headed
    assert "必须写列名" in headed[0].text
    assert headed[0].text.startswith("## ") is False


def test_over_800_chars_splits_again():
    from rsi_boot.rag.chunker import chunk_text

    body = "列名必须写全。" * 120
    assert len(body) > 800
    chunks = chunk_text(body)
    assert len(chunks) >= 2
    assert all(len(c.text) <= 800 for c in chunks)
    assert "".join(c.text for c in chunks) == body
