"""rsi wipe：一键清掉文件记忆树与指纹，保留 identity。"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from rsi_boot.__main__ import build_parser
from rsi_boot.cli.wipe_command import (
    WIPE_TREES,
    _is_holder_cmdline,
    run_wipe,
    wipe_project_memory,
)
from rsi_boot.ux.messages import t


def test_cli_wipe_flags():
    args = build_parser().parse_args(["wipe", "--yes", "--project-root", "D:/proj"])
    assert args.command == "wipe"
    assert args.yes is True
    assert args.project_root == "D:/proj"


def test_wipe_requires_yes(tmp_path: Path, monkeypatch):
    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    yaml_path = rsi / "memory" / "conventions" / "note--abcd1234.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("id: x\n", encoding="utf-8")
    (rsi / "rsi.db").write_bytes(b"x")
    monkeypatch.setenv("RSI_LANG", "zh")
    result = wipe_project_memory(tmp_path, yes=False, lang="zh")
    assert result["ok"] is False
    assert yaml_path.is_file()
    assert (rsi / "rsi.db").is_file()
    assert result["message"] == t("WIPE_HINT", "zh")


def test_wipe_yes_removes_memory_yaml_keeps_identity(tmp_path: Path, monkeypatch, capsys):
    rsi = tmp_path / ".rsi"
    yaml_path = rsi / "memory" / "conventions" / "note--abcd1234.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("title: note\n", encoding="utf-8")
    (rsi / "identity.json").write_text('{"project_id":"p1"}', encoding="utf-8")
    monkeypatch.setattr("rsi_boot.cli.wipe_command.stop_db_holders", lambda _root: [])
    monkeypatch.setenv("RSI_LANG", "zh")

    args = Namespace(yes=True, project_root=str(tmp_path), lang="zh")
    assert run_wipe(args) == 0
    assert not yaml_path.exists()
    assert (rsi / "identity.json").is_file()
    out = capsys.readouterr().out
    assert t("WIPE_DONE", "zh") in out
    assert t("WIPE_KEPT", "zh", path="identity.json") in out


def test_wipe_yes_removes_reading_packs_and_relearn_skill(tmp_path: Path, monkeypatch):
    rsi = tmp_path / ".rsi"
    packs = rsi / "state" / "reading-packs" / "index.yaml"
    packs.parent.mkdir(parents=True)
    packs.write_text("packs: []\n", encoding="utf-8")
    skill = rsi / "memory" / "skills" / "rsi-relearn--abcd1234.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text("title: rsi-relearn\npayload:\n  name: rsi-relearn\n", encoding="utf-8")
    (rsi / "identity.json").write_text('{"project_id":"p1"}', encoding="utf-8")
    monkeypatch.setattr("rsi_boot.cli.wipe_command.stop_db_holders", lambda _root: [])

    result = wipe_project_memory(tmp_path, yes=True, lang="zh")
    assert result["ok"] is True
    assert not packs.exists()
    assert not packs.parent.exists()
    assert not skill.exists()
    assert (rsi / "identity.json").is_file()


def test_wipe_yes_removes_all_five_trees(tmp_path: Path, monkeypatch):
    rsi = tmp_path / ".rsi"
    planted = []
    for tree in WIPE_TREES:
        path = rsi / tree / "keep-me.txt"
        path.parent.mkdir(parents=True)
        path.write_text(tree, encoding="utf-8")
        planted.append(path)
    (rsi / "identity.json").write_text('{"project_id":"p1"}', encoding="utf-8")
    monkeypatch.setattr("rsi_boot.cli.wipe_command.stop_db_holders", lambda _root: [])

    result = wipe_project_memory(tmp_path, yes=True, lang="zh")
    assert result["ok"] is True
    for path in planted:
        assert not path.exists()
        assert not path.parent.exists()
    assert WIPE_TREES == ("memory", "logs", "cache", "audit", "state")
    assert (rsi / "identity.json").is_file()


def test_wipe_also_deletes_leftover_rsi_db(tmp_path: Path, monkeypatch):
    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    (rsi / "rsi.db").write_bytes(b"db")
    (rsi / "rsi.db-wal").write_bytes(b"wal")
    (rsi / "rsi.db-shm").write_bytes(b"shm")
    (rsi / "manifest.json").write_text("{}", encoding="utf-8")
    (rsi / "identity.json").write_text('{"project_id":"p1"}', encoding="utf-8")
    monkeypatch.setattr("rsi_boot.cli.wipe_command.stop_db_holders", lambda _root: [])

    result = wipe_project_memory(tmp_path, yes=True)
    assert result["ok"] is True
    assert not (rsi / "rsi.db").exists()
    assert not (rsi / "rsi.db-wal").exists()
    assert not (rsi / "rsi.db-shm").exists()
    assert not (rsi / "manifest.json").exists()
    assert (rsi / "identity.json").is_file()


def test_holder_match_skips_path_only(tmp_path: Path):
    cursor = f'Cursor.exe --folder "{tmp_path}"'
    pytest_cmd = f'python -m pytest {tmp_path / "tests"}'
    serve = f'rsi serve --project-root {tmp_path}'
    module = f'python -m rsi_boot serve --project-root {tmp_path}'
    assert _is_holder_cmdline(cursor, tmp_path) is False
    assert _is_holder_cmdline(pytest_cmd, tmp_path) is False
    assert _is_holder_cmdline(serve, tmp_path) is True
    assert _is_holder_cmdline(module, tmp_path) is True


def test_readme_migrate_then_serve_and_wipe():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "rsi wipe --yes" in text
    assert "rsi memory migrate" in text
    assert "rsi serve" in text
    assert text.find("rsi memory migrate") < text.find("rsi serve")
    assert "rsi-convention" in text
    assert "约定" in text or "convention" in text.lower()
