"""ISSUE-5：Windows PowerShell 把 stderr 渲染为红色 NativeCommandError 块。

修复：CLI 子命令默认 WARNING + stdout；--verbose 恢复 INFO + stderr；
serve（MCP stdio）维持 INFO + stderr 不变。
"""

import logging
import sys

import pytest

from rsi_boot.__main__ import build_parser, main
from rsi_boot.common import logger as logger_mod


@pytest.fixture(autouse=True)
def _reset_logging():
    root = logging.getLogger("rsi_boot")
    for h in list(root.handlers):
        root.removeHandler(h)
    logger_mod._CONFIGURED = False
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    logger_mod._CONFIGURED = False


def test_setup_logging_default_unchanged_for_serve():
    """serve 兼容：缺省仍 INFO + stderr（stdio 协议要求 stdout 纯净）"""
    logger_mod.setup_logging()
    root = logging.getLogger("rsi_boot")
    assert root.level == logging.INFO
    assert root.handlers[0].stream is sys.stderr


def test_setup_logging_cli_mode_stdout_warning():
    logger_mod.setup_logging("WARNING", stream=sys.stdout)
    root = logging.getLogger("rsi_boot")
    assert root.level == logging.WARNING
    assert root.handlers[0].stream is sys.stdout


def test_cli_subcommands_accept_verbose():
    parser = build_parser()
    for argv in (
        ["init", "--verbose"],
        ["serve", "--verbose"],
        ["bootstrap", "--verbose"],
        ["recall", "task", "--verbose"],
        ["knowledge", "list", "--verbose"],
    ):
        assert parser.parse_args(argv).verbose is True
    assert parser.parse_args(["init"]).verbose is False


def test_init_no_stderr_red_block(tmp_path, monkeypatch, capsys):
    """rsi init 默认：stderr 零输出（红块根因消除），迁移 INFO 不显示"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    monkeypatch.setattr(sys, "argv", ["rsi", "init"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "初始化完成" in captured.out


def test_init_verbose_restores_info_stderr(tmp_path, monkeypatch, capsys):
    """rsi init --verbose：迁移 INFO 日志恢复到 stderr"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    monkeypatch.setattr(sys, "argv", ["rsi", "init", "--verbose"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "应用迁移" in captured.err  # migrate.py 的 INFO 日志
