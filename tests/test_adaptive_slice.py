from pathlib import Path

from rsi_boot.bootstrap import build_runtime
from rsi_boot.scanner.document_scanner import slice_document
from rsi_boot.scanner.incremental import ingest_document
from rsi_boot.scanner.validator import DedupSet, estimate_tokens


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


def test_heading_file_with_three_sections_writes_index(tmp_path: Path):
    p = tmp_path / "guide.md"
    p.write_text(
        "# One\n\n" + "内容一。" * 80 + "\n\n"
        "# Two\n\n" + "内容二。" * 80 + "\n\n"
        "# Three\n\n" + "内容三。" * 80,
        encoding="utf-8",
    )
    result = slice_document(p)
    sections = [c for c in result.chunks if c.kind == "section"]
    indexes = [c for c in result.chunks if c.kind == "index"]
    assert len(sections) >= 3
    assert len(indexes) == 1
    assert result.index_written == 1
    assert "One" in indexes[0].content
    assert "Two" in indexes[0].content
    assert "Three" in indexes[0].content


def test_chunk_kwargs_from_config():
    from rsi_boot.scanner.document_scanner import chunk_kwargs_from_config

    assert chunk_kwargs_from_config(None) == {}
    assert chunk_kwargs_from_config({}) == {}
    assert chunk_kwargs_from_config({
        "bootstrap": {"chunk": {"target_tokens": 200, "min_tokens": 40, "max_tokens": 800}},
    }) == {"target_tokens": 200, "min_tokens": 40, "max_tokens": 800}


def test_default_yaml_has_bootstrap_chunk():
    from importlib import resources

    import yaml

    text = resources.files("rsi_boot.config").joinpath("default.yaml").read_text(encoding="utf-8")
    chunk = yaml.safe_load(text)["bootstrap"]["chunk"]
    assert chunk["target_tokens"] == 400
    assert chunk["min_tokens"] == 80
    assert chunk["max_tokens"] == 1500


async def test_ingest_honors_chunk_config_and_tags_index(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "guide.md").write_text(
        "# One\n\n" + "内容一。" * 80 + "\n\n"
        "# Two\n\n" + "内容二。" * 80 + "\n\n"
        "# Three\n\n" + "内容三。" * 80,
        encoding="utf-8",
    )
    (root / "rsi-boot.yaml").write_text(
        "bootstrap:\n  chunk:\n    target_tokens: 200\n    min_tokens: 40\n    max_tokens: 800\n",
        encoding="utf-8",
    )
    import rsi_boot.scanner.incremental as inc

    seen: dict = {}
    orig = inc.slice_document

    def _spy(path, **kwargs):
        seen.update(kwargs)
        return orig(path, **kwargs)

    monkeypatch.setattr(inc, "slice_document", _spy)
    rt = await build_runtime(project_root=root)
    try:
        await ingest_document(
            rt, root, rt.project_id, root / "guide.md", DedupSet(),
            status="active", tags=["signal:docs"],
        )
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT tags FROM knowledge_items WHERE project_id = ?",
            (rt.project_id,),
        ) as cur:
            tags = [r["tags"] or "" for r in await cur.fetchall()]
    finally:
        await rt.close()
    assert seen.get("target_tokens") == 200
    assert seen.get("min_tokens") == 40
    assert seen.get("max_tokens") == 800
    assert any("signal:doc-index" in t for t in tags)
