from pathlib import Path

import pytest
from pydantic import ValidationError

from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.types import (
    LEGACY_TYPE_MAP,
    MEMORY_TYPES,
    MemoryDoc,
    status_from_path,
    type_from_legacy,
)


def test_teaching_pending_is_active():
    assert status_from_path("memory/teaching-cases/pending/x.yaml") == "active"


def test_norm_pending_is_review():
    assert status_from_path("memory/pending/prohibitions/x.yaml") == "pending_review"


def test_legacy_experience():
    typ, extra = type_from_legacy("experience")
    assert typ == "convention"
    assert extra["legacy_type"] == "experience"


def test_legacy_architecture_and_faq():
    typ, extra = type_from_legacy("architecture")
    assert typ == "documentation"
    assert extra["legacy_type"] == "architecture"
    typ, extra = type_from_legacy("faq")
    assert typ == "documentation"
    assert extra["legacy_type"] == "faq"


def test_legacy_unlisted_passthrough():
    typ, extra = type_from_legacy("prohibition")
    assert typ == "prohibition"
    assert extra == {}


def test_archive_is_archived():
    assert status_from_path("memory/archive/prohibitions/x.yaml") == "archived"


def test_official_memory_is_active():
    assert status_from_path("memory/prohibitions/x.yaml") == "active"


def test_memory_types_and_legacy_map():
    assert MEMORY_TYPES == (
        "prohibition",
        "convention",
        "documentation",
        "skill",
        "episode",
        "gene_case",
        "teaching_case",
        "pattern",
    )
    assert LEGACY_TYPE_MAP == {
        "experience": "convention",
        "architecture": "documentation",
        "faq": "documentation",
    }


def test_memory_doc_accepts_valid():
    doc = MemoryDoc(
        id="a" * 32,
        type="prohibition",
        title="禁止 SELECT *",
        content="必须列字段",
    )
    assert doc.status == "active"
    assert doc.tags == []
    assert doc.extra == {}
    assert doc.payload == {}


def test_memory_doc_rejects_unknown_top_level():
    with pytest.raises(ValidationError):
        MemoryDoc(
            id="a" * 32,
            type="prohibition",
            title="t",
            content="c",
            unknown_field="nope",
        )


def test_memory_doc_id_must_be_32_hex():
    with pytest.raises(ValidationError):
        MemoryDoc(id="NOTHEX", type="prohibition", title="t", content="c")
    with pytest.raises(ValidationError):
        MemoryDoc(id="A" * 32, type="prohibition", title="t", content="c")
    with pytest.raises(ValidationError):
        MemoryDoc(id="a" * 31, type="prohibition", title="t", content="c")


def test_memory_doc_title_and_content_max():
    with pytest.raises(ValidationError):
        MemoryDoc(id="b" * 32, type="convention", title="t" * 121, content="c")
    with pytest.raises(ValidationError):
        MemoryDoc(id="b" * 32, type="convention", title="t", content="c" * 20001)
    MemoryDoc(id="b" * 32, type="convention", title="t" * 120, content="c" * 20000)


def test_skill_body_alias_becomes_content():
    doc = MemoryDoc.model_validate(
        {
            "id": "c" * 32,
            "type": "skill",
            "title": "named params",
            "body": "use named parameters",
            "description": "skill card",
        }
    )
    assert doc.content == "use named parameters"


def test_official_and_pending_dirs(tmp_path: Path):
    assert official_dir(tmp_path, "prohibition") == tmp_path / "memory" / "prohibitions"
    assert official_dir(tmp_path, "convention") == tmp_path / "memory" / "conventions"
    assert official_dir(tmp_path, "documentation") == tmp_path / "memory" / "documentation"
    assert official_dir(tmp_path, "skill") == tmp_path / "memory" / "skills"
    assert official_dir(tmp_path, "episode") == tmp_path / "memory" / "episodes"
    assert official_dir(tmp_path, "gene_case") == tmp_path / "memory" / "gene-map"
    assert official_dir(tmp_path, "teaching_case") == tmp_path / "memory" / "teaching-cases"
    assert official_dir(tmp_path, "pattern") == tmp_path / "memory" / "patterns"
    assert pending_dir(tmp_path, "prohibition") == tmp_path / "memory" / "pending" / "prohibitions"
    assert pending_dir(tmp_path, "convention") == tmp_path / "memory" / "pending" / "conventions"
    assert pending_dir(tmp_path, "skill") == tmp_path / "memory" / "pending" / "skills"
    with pytest.raises(ValueError):
        pending_dir(tmp_path, "teaching_case")
    with pytest.raises(ValueError):
        pending_dir(tmp_path, "pattern")
