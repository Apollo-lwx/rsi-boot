"""P2.6 bootstrap 深度信号层测试（§10.9）：AST / Git / 对话 / 关联推理 / 增量归档"""

import shutil
import subprocess
from pathlib import Path

import pytest

from rsi_boot.scanner.code_scanner import (
    aggregate_imports,
    extract_generic_skeleton,
    extract_python_skeleton,
    group_by_directory,
    scan_code,
)
from rsi_boot.scanner.conversation_scanner import scan_conversations
from rsi_boot.scanner.correlation_engine import (
    correlate_commit_files,
    correlate_doc_code,
    correlate_test_code,
    summarize_correlations,
)
from rsi_boot.scanner.git_analyzer import analyze_git
from rsi_boot.scanner.incremental import archive_missing_signals
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService


# ---------- code_scanner ----------


def test_python_skeleton_extracts_symbols():
    source = '''"""用户模块"""
import os
import django
from fastapi import FastAPI

class UserService(BaseService):
    def get_user(self, user_id):
        pass
    def _private(self):
        pass

def create_user(name, email):
    """创建用户"""
    pass
'''
    s = extract_python_skeleton(source, "src/user.py")
    assert s is not None
    assert s.docstring == "用户模块"
    assert "class UserService(BaseService)" in s.symbols
    assert "def create_user(name, email)" in s.symbols
    assert any("UserService.get_user" in x for x in s.symbols)
    assert not any("_private" in x for x in s.symbols)  # 私有方法不入骨架
    assert "django" in s.imports and "fastapi" in s.imports


def test_python_skeleton_syntax_error_returns_none():
    assert extract_python_skeleton("def broken(:", "bad.py") is None


def test_generic_skeleton_js():
    source = "class UserController {}\nfunction fetchUser(id) {}\nconst helper = async () => {}"
    s = extract_generic_skeleton(source, "web/user.js", ".js")
    assert "UserController" in s.symbols
    assert "fetchUser" in s.symbols


def test_scan_code_and_aggregate(tmp_path):
    (tmp_path / "a.py").write_text("import flask\ndef view(): pass", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "b.py").write_text("import flask\nimport requests\nclass API: pass", encoding="utf-8")
    skeletons = scan_code(tmp_path, [tmp_path / "a.py", sub / "b.py"])
    assert len(skeletons) == 2
    counter = aggregate_imports(skeletons)
    assert counter["flask"] == 2
    groups = group_by_directory(skeletons)
    assert set(groups) == {".", "pkg"}


# ---------- git_analyzer ----------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "main.py").write_text("print('v1')", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: initial commit")
    (repo / "main.py").write_text("print('v2')", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix: correct output")
    (repo / "README.md").write_text("# demo", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "docs: add readme")
    return repo


def test_git_analyzer_conventional(git_repo):
    insights = analyze_git(git_repo)
    assert insights is not None
    assert insights.total_commits == 3
    assert insights.conventional_ratio == 1.0
    assert insights.commit_style == "conventional"
    assert insights.commit_types.get("feat") == 1
    assert "Dev <dev@example.com>" in insights.authors


def test_git_analyzer_ownership(git_repo):
    insights = analyze_git(git_repo)
    assert insights.ownership.get("main.py") == "dev@example.com"
    assert "main.py" in insights.top_files


def test_git_analyzer_not_a_repo(tmp_path):
    assert analyze_git(tmp_path) is None


# ---------- conversation_scanner ----------


def test_conversation_patterns(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# 变更记录\n\n"
        "我们决定从 Flask 迁移到 FastAPI，因为需要原生异步支持和自动 OpenAPI 文档。\n\n"
        "如何部署到生产环境？\n\n"
        "其他普通段落不含信号。\n",
        encoding="utf-8",
    )
    patterns = scan_conversations(tmp_path, [changelog])
    kinds = {p.kind for p in patterns}
    assert "decision" in kinds
    assert "question" in kinds
    assert any("FastAPI" in p.text for p in patterns)


def test_conversation_skips_json(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"chat": "决定迁移"}', encoding="utf-8")
    assert scan_conversations(tmp_path, [state]) == []


# ---------- correlation_engine ----------


def test_correlate_test_code(tmp_path):
    (tmp_path / "tests").mkdir()
    test_f = tmp_path / "tests" / "test_user.py"
    test_f.write_text("def test_x(): pass", encoding="utf-8")
    code_f = tmp_path / "user.py"
    code_f.write_text("class User: pass", encoding="utf-8")

    pairs = correlate_test_code([test_f], [code_f], tmp_path)
    assert len(pairs) == 1
    assert pairs[0].kind == "test_code"
    assert pairs[0].target == "user.py"


def test_correlate_doc_code():
    from rsi_boot.scanner.code_scanner import ModuleSkeleton

    skeletons = [ModuleSkeleton(rel_path="src/user_auth.py", language="python")]
    pairs = correlate_doc_code({"docs/auth.md": "user auth 指南"}, skeletons)
    assert len(pairs) == 1
    assert pairs[0].target == "src/user_auth.py"


def test_correlate_commit_files(git_repo):
    insights = analyze_git(git_repo)
    pairs = correlate_commit_files(insights)
    assert pairs
    assert all(p.kind == "commit_file" for p in pairs)
    summary = summarize_correlations(pairs)
    assert "commit_file" in summary


# ---------- incremental ----------


async def test_archive_missing_signals(db, base_config):
    service = KnowledgeService(db, KnowledgeRetriever(db, base_config))
    item = KnowledgeItem(project_id="p1", title="旧文档", content="x" * 300, source_url="docs/old.md")
    await service.add(item)

    class _Rt:
        pass

    rt = _Rt()
    rt.db = db
    archived = await archive_missing_signals(rt, "p1", {"docs/old.md": "abc"}, current_files=set())
    assert archived == 1

    conn = await db.connect()
    async with conn.execute("SELECT status FROM knowledge_items WHERE source_url = 'docs/old.md'") as cur:
        row = await cur.fetchone()
    assert row["status"] == "archived"

    # 文件仍存在时不归档
    archived = await archive_missing_signals(rt, "p1", {"docs/old.md": "abc"}, current_files={"docs/old.md"})
    assert archived == 0
