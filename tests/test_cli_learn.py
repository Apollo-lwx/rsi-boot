"""CLI: rsi learn — need action, teach_record --file, locked copy."""

from __future__ import annotations

import yaml


def test_cli_learn_need_action(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.learn_command import run_learn
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    assert run_learn([]) == 2
    assert capsys.readouterr().err.strip() == t("LEARN_NEED_ACTION", "zh")


def test_cli_learn_lang_overrides_env(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.learn_command import run_learn
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "en")
    assert run_learn(["--lang", "zh"]) == 2
    assert capsys.readouterr().err.strip() == t("LEARN_NEED_ACTION", "zh")


def test_cli_learn_teach_record_file(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.learn_command import run_learn
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    lesson = tmp_path / "lesson.yaml"
    lesson.write_text(
        yaml.safe_dump(
            {
                "wrong_action": "SELECT *",
                "correct_fix": "列出列名",
                "error_signature": "select-star",
                "failure_type": "sql",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert run_learn(["teach_record", "--file", str(lesson)]) == 0
    captured = capsys.readouterr()
    teaching = list((tmp_path / ".rsi" / "memory" / "teaching-cases").glob("*.yaml"))
    manual = list((tmp_path / ".rsi" / "memory" / "gene-map" / "manual").glob("*.yaml"))
    assert teaching
    assert manual
    rel = yaml.safe_load(teaching[0].read_text(encoding="utf-8"))["path"]
    assert captured.out.strip() == t("TEACH_RECORDED", "zh", path=rel)
    assert not list((tmp_path / ".rsi" / "memory" / "prohibitions").glob("*.yaml"))


def test_cli_learn_teach_record_needs_fix(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.learn_command import run_learn
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    lesson = tmp_path / "lesson.yaml"
    lesson.write_text("correct_fix: '   '\nerror_signature: x\n", encoding="utf-8")

    assert run_learn(["teach_record", "--file", str(lesson)]) == 2
    assert capsys.readouterr().err.strip() == t("TEACH_NEED_FIX", "zh")


def test_cli_learn_promote_not_in_phase(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.learn_command import run_learn
    from rsi_boot.ux.messages import t

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    assert run_learn(["promote"]) == 2
    assert capsys.readouterr().err.strip() == t("PHASE_PROMOTE", "zh")


def test_cli_learn_help_avoids_db_words():
    from rsi_boot.cli.learn_command import _parser

    text = _parser().format_help()
    assert "数据库" not in text
    assert "rsi.db" not in text
