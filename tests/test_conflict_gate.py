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


def test_gate_drafts_reports_progress():
    hits: list[int] = []
    drafts = [
        DraftItem(
            f"约定{i}",
            f"允许使用 uniquephrase{i} 作为约定。" * 4,
            "convention",
            f"a{i}.md",
            ["signal:docs"],
            "docs",
        )
        for i in range(4)
    ]
    gate_drafts(drafts, [], Path("."), on_progress=hits.append)
    assert hits
    assert max(hits) >= 4


def test_incoherent_pair_loop_reports_progress():
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
    hits: list[int] = []
    indexed_at: list[int] = []
    totals: list[int] = []

    def on_match_start(n: int) -> None:
        indexed_at.append(len(hits))
        totals.append(n)

    gate_drafts(
        drafts, [], Path("."),
        on_progress=hits.append, on_match_start=on_match_start,
    )
    assert indexed_at
    assert len(hits) > indexed_at[0], "极性配对必须继续打进度，不能停在索引 100%"
    assert totals and totals[0] >= 1


def test_usage_heading_is_not_permissive_conflict():
    """「使用」出现在普通叙述里不得当成允许，否则会炸出数万对。"""
    drafts = [
        DraftItem(
            "如何使用 MySQL",
            "本章说明如何使用 MySQL 连接池。" * 4,
            "documentation",
            "docs/mysql.md",
            ["signal:docs"],
            "docs",
        ),
        DraftItem(
            "禁止：eval",
            "禁止使用 eval 执行用户输入。" * 4,
            "prohibition",
            "rules.md",
            ["signal:rules"],
            "rules",
        ),
    ]
    result = gate_drafts(drafts, [], Path("."))
    assert not any(c.conflict_type == "incoherent" for c in result.conflicts)


def test_prefer_is_still_permissive():
    drafts = [
        DraftItem(
            "禁止：httpxclient",
            "禁止 httpxclient 作为入参。" * 4,
            "prohibition",
            "auto-a",
            ["signal:rules"],
            "rules",
        ),
        DraftItem(
            "HTTP 客户端",
            "优先 httpxclient 作为默认客户端。" * 4,
            "convention",
            "docs/http.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [], Path("."))
    assert any(c.conflict_type == "incoherent" for c in result.conflicts)


def test_oversized_polar_bucket_is_sampled_not_dropped():
    drafts = []
    for i in range(10):
        drafts.append(DraftItem(
            f"禁止{i}",
            "禁止 sharedtokenxyz 作为入参。" * 4,
            "prohibition",
            f"ban{i}.md",
            ["signal:rules"],
            "rules",
        ))
        drafts.append(DraftItem(
            f"允许{i}",
            "允许 sharedtokenxyz 作为约定。" * 4,
            "convention",
            f"ok{i}.md",
            ["signal:docs"],
            "docs",
        ))
    result = gate_drafts(drafts, [], Path("."))
    n = sum(1 for c in result.conflicts if c.conflict_type == "incoherent")
    assert 1 <= n <= 80


def test_generic_identifier_mysql_is_not_doc_code():
    from rsi_boot.scanner.code_scanner import ModuleSkeleton

    sk = ModuleSkeleton(rel_path="plugins/mysql.py", language="python", symbols=["MysqlPlugin"])
    drafts = [
        DraftItem(
            "MySQL",
            "See MySQL and InnoDB in the reference. " * 8,
            "documentation",
            "docs/mysql.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [sk], Path("."))
    assert not any(c.conflict_type == "doc_code" for c in result.conflicts)
