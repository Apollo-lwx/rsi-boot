from pathlib import Path

from rsi_boot.scanner.conflict_gate import DraftItem, gate_drafts


def test_versioned_filenames_hold_both(tmp_path: Path):
    (tmp_path / "foo-v1.0.md").write_text("# Foo\n\nold api\n", encoding="utf-8")
    (tmp_path / "foo-v1.1.md").write_text("# Foo\n\nnew api\n", encoding="utf-8")
    drafts = [
        DraftItem("Foo", "old api " * 20, "documentation", "foo-v1.0.md", ["signal:docs"], "docs"),
        DraftItem("Foo", "new api " * 20, "documentation", "foo-v1.1.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [], tmp_path)
    assert "foo-v1.0.md" in result.hold_sources
    assert "foo-v1.1.md" in result.hold_sources
    assert any(c.conflict_type == "version" for c in result.conflicts)


def test_doc_missing_class_holds_doc_only():
    from rsi_boot.scanner.code_scanner import ModuleSkeleton

    sk = ModuleSkeleton(rel_path="user.py", language="python", symbols=["User"])
    drafts = [
        DraftItem(
            "User",
            "See class MissingThing in the service layer. " * 5,
            "documentation",
            "docs/user.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [sk], Path("."))
    assert "docs/user.md" in result.hold_sources
    assert any(c.conflict_type == "doc_code" for c in result.conflicts)


def test_polarity_holds_both():
    drafts = [
        DraftItem(
            "禁止：pydantic",
            "禁止使用 pydantic 作为入参。 " * 4,
            "prohibition",
            "auto-a",
            ["signal:rules"],
            "rules",
        ),
        DraftItem(
            "API 用 pydantic",
            "允许使用 pydantic 校验请求体。 " * 4,
            "convention",
            "docs/api.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [], Path("."))
    assert result.hold_sources >= {"auto-a", "docs/api.md"}
