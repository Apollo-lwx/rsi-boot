from pathlib import Path

from rsi_boot.scanner.document_scanner import slice_document
from rsi_boot.scanner.validator import estimate_tokens


def test_tiny_headings_merge(tmp_path: Path):
    body = "\n".join(f"## H{i}\n\nshort.\n" for i in range(10))
    p = tmp_path / "a.md"
    p.write_text(body, encoding="utf-8")
    result = slice_document(p)
    assert len(result.chunks) < 10
    assert result.merged_tiny >= 1


def test_huge_body_splits_and_writes_index(tmp_path: Path):
    p = tmp_path / "big.md"
    p.write_text("x" * 6000, encoding="utf-8")  # 1500 tokens
    result = slice_document(p, max_tokens=400)
    assert result.chunks
    assert all(estimate_tokens(c.content) <= 400 for c in result.chunks if c.kind != "index")
    assert any(c.kind == "index" for c in result.chunks) or result.index_written >= 1


def test_license_is_single_digest(tmp_path: Path):
    p = tmp_path / "LICENSE"
    p.write_text("MIT " + ("copyright line\n" * 80), encoding="utf-8")
    result = slice_document(p)
    assert len(result.chunks) == 1
    assert result.chunks[0].kind == "digest"
