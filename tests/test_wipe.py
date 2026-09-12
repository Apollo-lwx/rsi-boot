"""rsi wipe：一键清掉项目库与指纹，保留 identity。"""

from __future__ import annotations

from pathlib import Path

from rsi_boot.__main__ import build_parser
from rsi_boot.cli.wipe_command import _is_holder_cmdline, wipe_project_memory


def test_cli_wipe_flags():
    args = build_parser().parse_args(["wipe", "--yes", "--project-root", "D:/proj"])
    assert args.command == "wipe"
    assert args.yes is True
    assert args.project_root == "D:/proj"


def test_wipe_requires_yes(tmp_path: Path):
    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    (rsi / "rsi.db").write_bytes(b"x")
    result = wipe_project_memory(tmp_path, yes=False)
    assert result["ok"] is False
    assert (rsi / "rsi.db").is_file()


def test_wipe_deletes_db_keeps_identity(tmp_path: Path, monkeypatch):
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


def test_readme_wipe_command():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "rsi wipe --yes" in text
    assert "manifest.json" in text
    assert "rsi.db" in text
