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


def _draft(title, body, source, signal="docs", ctype="convention"):
    return DraftItem(title, body, ctype, source, [f"signal:{signal}"], signal)


def test_plaintext_vs_encrypted_password_not_paired():
    """对象不同（明文 vs 加密密码）：标题/目录桶对不上，不成对。"""
    drafts = [
        _draft("密码存储", "禁止写入明文密码到数据库。" * 6, "docs/security.md"),
        _draft("密码存储", "允许写入加密密码到数据库。" * 6, "docs/crypto.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert not any(c.conflict_type == "incoherent" for c in result.conflicts)
    assert not result.hold_sources


def test_same_object_opposite_polarity_opens_incoherent_local():
    """同一约束对象（pydantic 入参）一禁一许 → 本地裁决开 incoherent 并 hold 两侧。"""
    drafts = [
        _draft("API 入参", "禁止使用 pydantic 作为入参。" * 6, "rules/a.md", signal="rules", ctype="prohibition"),
        _draft("API 入参", "允许使用 pydantic 校验请求体。" * 6, "docs/api.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    conflict = next(c for c in result.conflicts if c.conflict_type == "incoherent")
    assert conflict.hold_sources == ["rules/a.md", "docs/api.md"]
    assert result.hold_sources >= {"rules/a.md", "docs/api.md"}


def test_same_title_bucket_pairs_near_duplicates():
    """同一规范化标题 + 正文 Jaccard 在 [0.35, 0.85) → 成对（local 下无极性不冲突）。"""
    drafts = [
        _draft("部署", "使用 docker compose 启动全部服务。" * 6, "docs/deploy-a.md"),
        _draft("部署", "使用 docker compose 启动基础服务，其余按需。" * 6, "docs/deploy-b.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    # 无极性相反 → 不开冲突；但 host 模式下应入包
    assert not result.conflicts
    host = gate_drafts(drafts, [], Path("."), judge="host")
    assert any(c.conflict_type == "incoherent" for c in host.conflicts)
    assert not host.hold_sources  # host 不 hold peer 对


def test_parent_dir_h1_bucket_pairs():
    """标题不同但同父目录 + 正文首个 `# ` 一级标题相同 → 成对。"""
    drafts = [
        _draft("缓存策略", "# 会话缓存\n\n允许使用 redis 缓存会话。" + "允许使用 redis 缓存会话。" * 5,
               "docs/arch/a.md"),
        _draft("会话缓存", "# 会话缓存\n\n禁止使用 redis 缓存会话。" + "禁止使用 redis 缓存会话。" * 5,
               "docs/arch/b.md", signal="rules", ctype="prohibition"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert any(c.conflict_type == "incoherent" for c in result.conflicts)


def test_jaccard_outside_band_not_paired():
    """正文几乎相同（>=0.85，复制）或几乎无关（<0.35）都不成对。"""
    same = "同一段说明文字。" * 30
    drafts = [
        _draft("相同", same, "a/x.md"),
        _draft("相同", same, "b/y.md"),
        _draft("无关", "完全不同的主题，讲操作系统调度。" * 8, "a/z.md"),
    ]
    host = gate_drafts(drafts, [], Path("."), judge="host")
    assert not host.conflicts


def test_candidate_cap_counts_omitted(monkeypatch):
    """peer 候选超 10000 时按距 0.85 截断并计数（用小常量 monkeypatch 验证）。"""
    import rsi_boot.scanner.conflict_gate as gate_mod
    drafts = []
    for i in range(30):
        drafts.append(_draft("同题", f"允许使用 token{i} 作为约定。" * 4, f"ok{i}.md"))
        drafts.append(_draft("同题", f"禁止 token{i} 作为入参。" * 4, f"ban{i}.md",
                             signal="rules", ctype="prohibition"))
    monkeypatch.setattr(gate_mod, "_MAX_CANDIDATES", 10)
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert len(result.conflicts) <= 10
    assert result.omitted_candidates >= 1
