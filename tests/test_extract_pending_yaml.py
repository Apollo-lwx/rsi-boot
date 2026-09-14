from pathlib import Path


import pytest


@pytest.mark.asyncio
async def test_run_daily_file_runtime_extracts_high_rated(tmp_path):
    from datetime import datetime, timezone

    from rsi_boot.learning.knowledge_extractor import KnowledgeExtractor
    from rsi_boot.memory.logstore import append_event
    from rsi_boot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / ".rsi")
    append_event(store.rsi_dir, {
        "id": "e" * 32,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "recall",
        "task": "怎么写查询",
        "excerpt": "必须列出列名，禁止 SELECT *",
        "rating": 5,
        "retrieved": [],
    })
    stats = await KnowledgeExtractor(store=store).run_daily()
    assert stats["collected"] >= 1
    assert stats["extracted"] >= 1
    pending = list((store.rsi_dir / "memory" / "pending" / "conventions").glob("*.yaml"))
    assert pending


def test_feedback_extract_only_pending_norms(tmp_path):
    from rsi_boot.learning.pipeline import extract_from_feedback
    paths = extract_from_feedback(
        tmp_path / ".rsi",
        action="rejected",
        comment="不要再用 SELECT *",
        retrieved=["a"*32],
    )
    assert paths
    for p in paths:
        rel = p.as_posix()
        assert rel.startswith("memory/pending/prohibitions") or rel.startswith("memory/pending/conventions")
        assert "gene-map" not in rel and "teaching-cases" not in rel and "patterns" not in rel


def test_feedback_extract_appends_candidates_jsonl(tmp_path):
    from rsi_boot.learning.pipeline import extract_from_feedback

    rsi = tmp_path / ".rsi"
    paths = extract_from_feedback(
        rsi,
        action="rejected",
        comment="不要再用 SELECT *",
        retrieved=["a" * 32],
    )
    assert paths
    log = rsi / "logs" / "candidates.jsonl"
    assert log.is_file()
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "a" * 32 in lines[0]

    written = [p for p in rsi.rglob("*") if p.is_file()]
    allowed = ("memory/pending/prohibitions", "memory/pending/conventions", "logs/candidates.jsonl")
    for path in written:
        rel = path.relative_to(rsi).as_posix()
        assert any(rel.startswith(prefix) for prefix in allowed)
        assert "gene-map" not in rel and "teaching-cases" not in rel and "patterns" not in rel


def test_feedback_extract_modified_writes_pending_convention(tmp_path):
    from rsi_boot.learning.pipeline import extract_from_feedback

    paths = extract_from_feedback(
        tmp_path / ".rsi",
        action="modified",
        comment="使用显式列名代替 SELECT *",
        retrieved=["b" * 32],
    )
    assert paths
    for p in paths:
        rel = p.as_posix()
        assert rel.startswith("memory/pending/conventions")
        assert "gene-map" not in rel and "teaching-cases" not in rel and "patterns" not in rel


def test_feedback_extract_skips_below_quality_threshold(tmp_path):
    from rsi_boot.learning.pipeline import extract_from_feedback

    rsi = tmp_path / ".rsi"
    assert extract_from_feedback(rsi, action="rejected", comment="   ", retrieved=["c" * 32]) == []
    assert extract_from_feedback(rsi, action="accepted", comment="不要再用 SELECT *") == []
    yaml_files = list((rsi / "memory").rglob("*.yaml")) if (rsi / "memory").exists() else []
    assert yaml_files == []
