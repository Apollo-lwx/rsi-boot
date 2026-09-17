"""跨项目记忆隔离：对齐 Superpowers——一个工作目录一份 .rsi/。

隔离靠目录，不靠全局库或路径哈希：
1. 记忆只写 <workspace>/.rsi/rsi.db
2. 库内 project_id 恒为 local（文件本身就是边界）
3. 工作区发现：已有 .rsi/ 则向上认领，否则就用 cwd（不爬到无关 git 根）
4. 画像也在该 .rsi/ 里，不进 ~/.rsi/global.db
5. 旧版 ~/.rsi/rsi.db 仅按目录名认领；default 须显式 adopt
"""

from __future__ import annotations

import json
from pathlib import Path

from rsi_boot.api.tools import knowledge_add_tool, knowledge_search_tool, recall_tool
from rsi_boot.bootstrap import build_runtime, project_db_path
from rsi_boot.core.models import KnowledgeItem, UserProfile
from rsi_boot.data.migrate import migrate
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.project import (
    WORKSPACE_PROJECT_ID,
    load_or_create_identity,
    project_scope,
    resolve_project_root,
)
from rsi_boot.services.legacy_migrate import (
    _journal_path,
    _load_journal,
    adopt_namespaces,
    list_legacy_namespaces,
    remap_to_workspace_id,
)


def test_same_folder_name_gets_separate_rsi_dirs(tmp_path):
    a = tmp_path / "workspace-a" / "frontend"
    b = tmp_path / "workspace-b" / "frontend"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    db_a, pid_a = project_scope(a)
    db_b, pid_b = project_scope(b)
    assert db_a == a / ".rsi" / "rsi.db"
    assert db_b == b / ".rsi" / "rsi.db"
    assert db_a != db_b
    assert pid_a == pid_b == WORKSPACE_PROJECT_ID


def test_identity_is_workspace_local(tmp_path):
    root = tmp_path / "old-name"
    root.mkdir()
    first = load_or_create_identity(root)
    assert first.project_id == WORKSPACE_PROJECT_ID
    renamed = tmp_path / "new-name"
    root.rename(renamed)
    second = load_or_create_identity(renamed)
    assert second.project_id == WORKSPACE_PROJECT_ID


def test_project_db_lives_under_workspace_not_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    db_path, pid = project_scope(root)
    assert db_path == root / ".rsi" / "rsi.db"
    assert db_path == project_db_path(root)
    assert pid == WORKSPACE_PROJECT_ID
    assert "home" not in str(db_path)


def test_resolve_walks_up_to_existing_rsi(tmp_path):
    root = tmp_path / "repo"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (root / ".rsi").mkdir()
    assert resolve_project_root(cwd=nested) == root.resolve()


def test_resolve_uses_cwd_not_parent_git(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "app"
    nested.mkdir()
    assert resolve_project_root(cwd=nested) == nested.resolve()


def test_resolve_does_not_claim_user_home_rsi(tmp_path, monkeypatch):
    """向上查找不得把 ~/.rsi 配置目录认成工作区（Windows 上 cwd 常在用户目录下）"""
    monkeypatch.setenv("RSI_HOME", str(Path.home() / ".rsi"))
    nested = tmp_path / "app"
    nested.mkdir()
    assert resolve_project_root(cwd=nested) == nested.resolve()


def test_ide_workspace_env_beats_wrong_cwd(tmp_path, monkeypatch):
    """Cursor 全局 MCP：cwd 常是 Cursor 用户目录，工作区在 WORKSPACE_FOLDER_PATHS"""
    workspace = tmp_path / "real-repo"
    workspace.mkdir()
    fake_cwd = tmp_path / "Cursor"
    fake_cwd.mkdir()
    monkeypatch.delenv("RSI_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("WORKSPACE_FOLDER_PATHS", str(workspace))
    assert resolve_project_root(cwd=fake_cwd) == workspace.resolve()


def test_vscode_cwd_not_trusted_as_workspace(tmp_path, monkeypatch):
    """VSCODE_CWD 是编辑器进程自身 cwd（如 D:\\tools\\cursor），不是工作区指示器；
    agent 终端继承它时不得盖过 cwd 认领，否则记忆会写进编辑器安装目录。"""
    editor_dir = tmp_path / "cursor-editor"
    editor_dir.mkdir()
    workspace = tmp_path / "real-repo"
    nested = workspace / "src"
    nested.mkdir(parents=True)
    (workspace / ".rsi").mkdir()
    monkeypatch.setenv("VSCODE_CWD", str(editor_dir))
    # cwd 向上有已认领 .rsi/：认领工作区，而不是编辑器目录
    assert resolve_project_root(cwd=nested) == workspace.resolve()
    # cwd 无 .rsi/：落 cwd 本身，同样不得落编辑器目录
    plain = tmp_path / "plain"
    plain.mkdir()
    assert resolve_project_root(cwd=plain) == plain.resolve()


def test_ide_workspace_file_uri(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    uri = workspace.resolve().as_uri()
    monkeypatch.delenv("RSI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACE_FOLDER_PATHS", raising=False)
    monkeypatch.setenv("CURSOR_WORKSPACE", uri)
    assert resolve_project_root(cwd=tmp_path / "elsewhere") == workspace.resolve()


def test_unexpanded_workspace_placeholder_falls_back_to_ide_env(tmp_path, monkeypatch):
    """Cursor 用户级 mcp.json 经常不展开 ${workspaceFolder}，字面量不得盖过 IDE 注入。"""
    workspace = tmp_path / "real-repo"
    workspace.mkdir()
    fake_cwd = tmp_path / "Cursor"
    fake_cwd.mkdir()
    monkeypatch.delenv("RSI_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("WORKSPACE_FOLDER_PATHS", str(workspace))
    assert resolve_project_root(
        explicit=Path("${workspaceFolder}"), cwd=fake_cwd,
    ) == workspace.resolve()
    assert resolve_project_root(
        explicit=fake_cwd / "${workspaceFolder}", cwd=fake_cwd,
    ) == workspace.resolve()


def test_cursor_workspace_root_env(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.delenv("RSI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACE_FOLDER_PATHS", raising=False)
    monkeypatch.setenv("CURSOR_WORKSPACE_ROOT", str(workspace))
    assert resolve_project_root(cwd=tmp_path / "elsewhere") == workspace.resolve()


def test_serve_help_points_at_rsi_project_root():
    from rsi_boot.__main__ import build_parser

    serve_help = None
    for action in build_parser()._subparsers._group_actions:
        for name, sub in action.choices.items():
            if name == "serve":
                serve_help = sub.format_help()
    assert serve_help is not None
    assert "RSI_PROJECT_ROOT" in serve_help


def test_serve_parser_accepts_project_root():
    from rsi_boot.__main__ import build_parser

    args = build_parser().parse_args(["serve", "--project-root", "/tmp/ws"])
    assert args.project_root == "/tmp/ws"


async def _add_active(runtime, title: str, content: str) -> str:
    return await runtime.knowledge.add(
        KnowledgeItem(project_id="should-be-rewritten", title=title, content=content, status="active")
    )


async def test_two_projects_do_not_share_knowledge(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()

    rt_a = await build_runtime(project_root=proj_a)
    rt_b = await build_runtime(project_root=proj_b)
    try:
        assert rt_a.project_id == rt_b.project_id == WORKSPACE_PROJECT_ID
        assert rt_a.store.rsi_dir == proj_a / ".rsi"
        assert rt_b.store.rsi_dir == proj_b / ".rsi"

        await _add_active(
            rt_a, "AlphaLedger 约定",
            "本仓库使用 AlphaLedger 作为账本名称，禁止在其它项目复用该专有名词。",
        )
        await _add_active(
            rt_b, "BetaVault 约定",
            "本仓库使用 BetaVault 作为保险库名称，禁止泄露到其它仓库。",
        )

        listed_a = await rt_a.knowledge.list(rt_a.project_id)
        listed_b = await rt_b.knowledge.list(rt_b.project_id)
        assert any("AlphaLedger" in (r.get("title") or "") for r in listed_a)
        assert not any("AlphaLedger" in (r.get("title") or "") for r in listed_b)
        assert any("BetaVault" in (r.get("title") or "") for r in listed_b)
        assert not any("BetaVault" in (r.get("title") or "") for r in listed_a)

        hit_a = await rt_a.recall.recall("AlphaLedger 账本", rt_b.project_id)
        titles_a = [i["title"] for i in hit_a["items"]] + [p["title"] for p in hit_a["prohibitions"]]
        assert any("AlphaLedger" in t for t in titles_a)

        leak = await rt_b.recall.recall("AlphaLedger 账本", rt_a.project_id)
        leak_titles = [i["title"] for i in leak["items"]] + [p["title"] for p in leak["prohibitions"]]
        assert not any("AlphaLedger" in t for t in leak_titles)
    finally:
        await rt_a.close()
        await rt_b.close()


async def test_mcp_tools_ignore_foreign_project_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()
    rt_a = await build_runtime(project_root=proj_a)
    rt_b = await build_runtime(project_root=proj_b)
    try:
        await knowledge_add_tool.handle(rt_a.knowledge, {
            "title": "Alpha 禁止串库",
            "content": "AlphaLedger 只属于 alpha 仓库，其它项目不得召回。",
            "project_id": rt_b.project_id,
        })
        # 写入必须落在 A 的绑定身份，而不是请求里的 B
        rows = await rt_a.knowledge.list(rt_a.project_id)
        assert len(rows) == 1
        assert await rt_b.knowledge.list(rt_b.project_id) == []

        searched = await knowledge_search_tool.handle(rt_b.knowledge, {
            "query": "AlphaLedger",
            "project_id": rt_a.project_id,
        })
        assert searched["items"] == []

        recalled = await recall_tool.handle(rt_b, {
            "task": "查阅 AlphaLedger 约定",
            "project_id": rt_a.project_id,
        })
        data = recalled["data"]
        leak = [i["title"] for i in data["items"]] + [p["title"] for p in data["prohibitions"]]
        assert not any("Alpha" in t for t in leak)
    finally:
        await rt_a.close()
        await rt_b.close()


async def test_profiles_stay_inside_workspace_rsi(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()
    rt_a = await build_runtime(project_root=proj_a)
    rt_b = await build_runtime(project_root=proj_b)
    try:
        await rt_a.profiles.upsert(UserProfile(
            user_id="u1", project_id=rt_a.project_id, expertise=["alpha-only", "pytest"],
        ))
        merged_a = await rt_a.profiles.get("u1", rt_a.project_id)
        merged_b = await rt_b.profiles.get("u1", rt_b.project_id)
        assert "alpha-only" in merged_a.expertise
        assert "alpha-only" not in merged_b.expertise
        assert rt_a.store.rsi_dir == proj_a / ".rsi"
        assert not hasattr(rt_a, "db")
        assert not (tmp_path / "home" / "global.db").exists()
        assert (proj_a / ".rsi" / "state" / "profile.yaml").is_file()
    finally:
        await rt_a.close()
        await rt_b.close()


async def _seed_legacy(home: Path, rows: list[tuple[str, str, str]]) -> Path:
    legacy = home / "rsi.db"
    db = SQLiteClient(legacy)
    await migrate(db)
    conn = await db.connect()
    now = "2026-01-01T00:00:00+00:00"
    for pid, title, content in rows:
        await conn.execute(
            "INSERT INTO knowledge_items (id, project_id, title, content, content_type, "
            "roles, tags, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'convention', "
            "'[]', '[]', 'active', ?, ?)",
            (title.replace(" ", "")[:16] + pid[:8], pid, title, content, now, now),
        )
    await conn.execute(
        "INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at) "
        "VALUES ('u1', '', ?, ?, ?)",
        (UserProfile(user_id="u1", project_id=None, expertise=["shared-global"]).model_dump_json(),
         now, now),
    )
    await conn.commit()
    await db.close()
    return legacy


async def test_legacy_named_namespace_migrates_default_does_not_leak(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(home))
    await _seed_legacy(home, [
        ("alpha", "旧 Alpha 记忆", "来自旧共享库的 AlphaLedger 条目，足够长以便检索。"),
        ("default", "串库水槽", "这段 default 记忆不该自动灌进每个新项目。"),
        ("other", "别人的项目", "other 命名空间不属于 alpha。"),
    ])
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()

    rt_a = await build_runtime(project_root=proj_a)
    rt_b = await build_runtime(project_root=proj_b)
    try:
        titles_a = [r["title"] for r in await rt_a.knowledge.list(rt_a.project_id, limit=50)]
        titles_b = [r["title"] for r in await rt_b.knowledge.list(rt_b.project_id, limit=50)]
        # Runtime 不再自动 connect/迁出 sqlite；隔离靠目录，默认命名空间不得泄漏
        assert "旧 Alpha 记忆" not in titles_a
        assert "串库水槽" not in titles_a
        assert "别人的项目" not in titles_a
        assert titles_b == []

        merged = await rt_a.profiles.get("u1", rt_a.project_id)
        assert "shared-global" not in merged.expertise
    finally:
        await rt_a.close()
        await rt_b.close()


async def test_migrate_adopt_default_into_current_project_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(home))
    await _seed_legacy(home, [
        ("default", "认领的水槽", "用户显式认领 default 到当前项目。"),
    ])
    proj = tmp_path / "gamma"
    proj.mkdir()
    rt = await build_runtime(project_root=proj)
    try:
        assert await rt.knowledge.list(rt.project_id) == []
        ns = await list_legacy_namespaces(home / "rsi.db")
        assert "default" in ns
        dest = SQLiteClient(project_db_path(proj))
        await migrate(dest)
        try:
            result = await adopt_namespaces(home / "rsi.db", dest, ["default"], rt.project_id)
        finally:
            await dest.close()
        assert result["knowledge_items"] >= 1
        from rsi_boot.services.memory_migrate import migrate_workspace

        await migrate_workspace(proj)
        titles = [r["title"] for r in await rt.knowledge.list(rt.project_id)]
        assert "认领的水槽" in titles
    finally:
        await rt.close()


async def test_same_folder_name_each_dest_db_adopts_once(tmp_path, monkeypatch):
    """journal 按目标库路径去重：两个 frontend 工作区各自认领一份，互不跳过。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(home))
    await _seed_legacy(home, [
        ("frontend", "旧前端约定", "来自旧共享库的 frontend 命名空间记忆，足够长。"),
    ])
    a = tmp_path / "workspace-a" / "frontend"
    b = tmp_path / "workspace-b" / "frontend"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    dest_a = SQLiteClient(project_db_path(a))
    dest_b = SQLiteClient(project_db_path(b))
    await migrate(dest_a)
    await migrate(dest_b)
    try:
        await adopt_namespaces(home / "rsi.db", dest_a, ["frontend"], WORKSPACE_PROJECT_ID)
        await adopt_namespaces(home / "rsi.db", dest_b, ["frontend"], WORKSPACE_PROJECT_ID)
        conn_a = await dest_a.connect()
        conn_b = await dest_b.connect()
        async with conn_a.execute("SELECT title FROM knowledge_items") as cur:
            titles_a = [r["title"] for r in await cur.fetchall()]
        async with conn_b.execute("SELECT title FROM knowledge_items") as cur:
            titles_b = [r["title"] for r in await cur.fetchall()]
        assert "旧前端约定" in titles_a
        assert "旧前端约定" in titles_b
        journal = _load_journal()
        dests = {
            Path(row["dest_db"]).resolve()
            for row in journal["adoptions"]
            if row.get("namespace") == "frontend"
        }
        assert dests == {dest_a.db_path.resolve(), dest_b.db_path.resolve()}
        assert _journal_path().is_file()
    finally:
        await dest_a.close()
        await dest_b.close()


async def test_remap_user_profiles_without_collision_rewrites_id(tmp_path):
    db = SQLiteClient(tmp_path / "rsi.db")
    await migrate(db)
    try:
        conn = await db.connect()
        now = "2026-01-01T00:00:00+00:00"
        await conn.execute(
            "INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at) "
            "VALUES ('u1', 'old-hash', '{\"keep\":\"old\"}', ?, ?)",
            (now, now),
        )
        await conn.commit()
        assert await remap_to_workspace_id(db, WORKSPACE_PROJECT_ID) >= 1
        async with conn.execute(
            "SELECT project_id, profile_data FROM user_profiles WHERE user_id = 'u1'"
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["project_id"] == WORKSPACE_PROJECT_ID
        assert "old" in rows[0]["profile_data"]
    finally:
        await db.close()


async def test_remap_user_profiles_collision_keeps_local(tmp_path):
    """旧哈希 project_id 与已有 local 并存时，remap 不得 UNIQUE 崩掉。"""
    db = SQLiteClient(tmp_path / "rsi.db")
    await migrate(db)
    try:
        conn = await db.connect()
        now = "2026-01-01T00:00:00+00:00"
        await conn.execute(
            "INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at) "
            "VALUES ('u1', 'old-hash', '{\"keep\":\"old\"}', ?, ?)",
            (now, now),
        )
        await conn.execute(
            "INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at) "
            "VALUES ('u1', ?, '{\"keep\":\"local\"}', ?, ?)",
            (WORKSPACE_PROJECT_ID, now, now),
        )
        await conn.execute(
            "INSERT INTO project_configs (project_id, config_data, created_at, updated_at) "
            "VALUES ('old-hash', '{\"src\":\"old\"}', ?, ?)",
            (now, now),
        )
        await conn.execute(
            "INSERT INTO project_configs (project_id, config_data, created_at, updated_at) "
            "VALUES (?, '{\"src\":\"local\"}', ?, ?)",
            (WORKSPACE_PROJECT_ID, now, now),
        )
        await conn.commit()

        updated = await remap_to_workspace_id(db, WORKSPACE_PROJECT_ID)
        assert updated >= 0

        async with conn.execute(
            "SELECT project_id, profile_data FROM user_profiles WHERE user_id = 'u1'"
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["project_id"] == WORKSPACE_PROJECT_ID
        assert "local" in rows[0]["profile_data"]

        async with conn.execute("SELECT project_id, config_data FROM project_configs") as cur:
            cfg = await cur.fetchall()
        assert len(cfg) == 1
        assert cfg[0]["project_id"] == WORKSPACE_PROJECT_ID
        assert "local" in cfg[0]["config_data"]
    finally:
        await db.close()


async def test_cmd_serve_defaults_to_warning_log(monkeypatch, tmp_path):
    from rsi_boot.__main__ import build_parser, cmd_serve

    levels: list[str] = []
    monkeypatch.setattr(
        "rsi_boot.__main__.setup_logging",
        lambda level="INFO", stream=None: levels.append(level),
    )
    monkeypatch.setattr("rsi_boot.__main__.resolve_project_root", lambda explicit=None: tmp_path)
    async def _run(*_a, **_k):
        return 0
    monkeypatch.setattr("rsi_boot.__main__.run_serve", _run)
    args = build_parser().parse_args(["serve"])
    assert await cmd_serve(args) == 0
    assert levels == ["WARNING"]


def test_identity_json_written(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    ident = load_or_create_identity(root)
    payload = json.loads((root / ".rsi" / "identity.json").read_text(encoding="utf-8"))
    assert ident.project_id == WORKSPACE_PROJECT_ID
    assert payload["project_id"] == WORKSPACE_PROJECT_ID
