"""bootstrap 判断旗标：非 dry-run 必须恰好一个。"""
import argparse
import json
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=False,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def _proj(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + "这是一段足够长的仓库原文。" * 40,
        encoding="utf-8",
    )
    return root


async def test_bootstrap_without_judge_flag_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 2
    assert "必须指定 --host-judge 或 --local-judge" in capsys.readouterr().err


async def test_bootstrap_with_both_judge_flags_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, host_judge=True, local_judge=True)) == 2
    assert "不能同时使用 --host-judge 与 --local-judge" in capsys.readouterr().err


async def test_bootstrap_dry_run_exempt_from_judge_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert not (root / ".rsi" / "manifest.json").exists()


def test_parser_accepts_judge_flags():
    from rsi_boot.__main__ import build_parser  # 现网 179 行已是 build_parser()
    parser = build_parser()
    args = parser.parse_args(["bootstrap", "--host-judge"])
    assert args.host_judge is True and args.local_judge is False
    args = parser.parse_args(["bootstrap", "--local-judge"])
    assert args.local_judge is True and args.host_judge is False


async def test_host_judge_writes_queue_with_full_content(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "a.md").write_text(
        "# 部署\n\n" + "允许使用 docker compose 启动全部服务。" * 8, encoding="utf-8")
    (root / "docs" / "b.md").write_text(
        "# 部署\n\n" + "禁止使用 docker compose 启动全部服务，只起基础镜像。" * 8,
        encoding="utf-8")
    assert await run_bootstrap(_args(root, host_judge=True)) == 0
    queue = root / ".rsi" / "host_judge_queue.json"
    assert queue.is_file()
    data = json.loads(queue.read_text(encoding="utf-8"))
    assert data["judge"] == "host"
    assert data["bootstrap_run_id"]
    assert data["items"], "同标题近似对必须入包"
    item = data["items"][0]
    assert item["conflict_id"]
    assert item["left"]["content"] and item["right"]["source"]
    assert item["recommended"] in ("keep_item", "keep_peer", "coexist")
    # host 不裁决 peer：两侧都不应因 peer 被 hold 成 pending_review
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "host"
    assert report["judge_queue_path"].endswith("host_judge_queue.json")


async def test_local_judge_deletes_stale_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    rsi = root / ".rsi"
    rsi.mkdir()
    stale = rsi / "host_judge_queue.json"
    stale.write_text('{"items": []}', encoding="utf-8")
    assert await run_bootstrap(_args(root, local_judge=True)) == 0
    assert not stale.exists()
    report = json.loads((rsi / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "local"
