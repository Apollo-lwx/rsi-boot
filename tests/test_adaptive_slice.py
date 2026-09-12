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


def test_huge_body_splits_by_target_tokens(tmp_path: Path):
    p = tmp_path / "big.md"
    p.write_text("x" * 6000, encoding="utf-8")  # 1500 tokens
    result = slice_document(p, max_tokens=1500, target_tokens=400)
    sections = [c for c in result.chunks if c.kind != "index"]
    assert len(sections) >= 3
    assert all(estimate_tokens(c.content) <= 1500 for c in sections)
    avg = sum(estimate_tokens(c.content) for c in sections) / len(sections)
    assert 250 <= avg <= 550


def test_post_merge_still_tiny_skipped_but_keeps_one(tmp_path: Path):
    body = "\n".join(f"## H{i}\n\nx\n" for i in range(5))
    p = tmp_path / "tiny.md"
    p.write_text(body, encoding="utf-8")
    result = slice_document(p, min_tokens=80)
    assert len(result.chunks) == 1
    assert result.skipped_tiny == 0


def test_post_merge_tiny_dropped_when_other_content_exists(tmp_path: Path):
    big = "word " * 200
    body = f"# Big\n\n{big}\n\n## Tiny\n\na\n"
    p = tmp_path / "mix.md"
    p.write_text(body, encoding="utf-8")
    result = slice_document(p, min_tokens=80)
    titles = [c.title for c in result.chunks if c.kind != "index"]
    assert "Big" in titles
    assert "Tiny" not in titles
    assert result.skipped_tiny >= 1


def test_license_is_single_digest(tmp_path: Path):
    p = tmp_path / "LICENSE"
    p.write_text("MIT " + ("copyright line\n" * 80), encoding="utf-8")
    result = slice_document(p)
    assert len(result.chunks) == 1
    assert result.chunks[0].kind == "digest"
