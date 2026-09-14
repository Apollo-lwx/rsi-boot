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
    assert "judge_queue_path" not in report
    assert "judge_candidates" not in report
    assert "judge_omitted" not in report
    assert "judge_unresolved" not in report
    assert "pack_count" in report


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


class _QueueFakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetchall(self):
        return self._rows


class _QueueFakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        return _QueueFakeCursor(self._rows)


class _QueueFakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def connect(self):
        return _QueueFakeConn(self._rows)


class _QueueFakeDetector:
    def __init__(self, rows):
        self._rows = rows

    async def list_conflicts(
        self, project_id, status="open", *, bootstrap_run_id=None, limit=100, offset=0,
    ):
        return self._rows[offset:offset + limit]


class _QueueFakeRuntime:
    def __init__(self, item_rows, conflict_rows):
        self.db = _QueueFakeDB(item_rows)
        self.conflict_detector = _QueueFakeDetector(conflict_rows)


async def test_host_judge_queue_same_pair_two_types_not_crossed(tmp_path):
    """同 (left, right) 检出 version + incoherent 两类时，队列项各拿各的标签。"""
    from rsi_boot.cli.bootstrap_command import _write_host_judge_queue
    from rsi_boot.scanner.conflict_gate import ConflictDraft, DraftItem, GateResult

    drafts = [
        DraftItem(title="Foo", content="旧版接口返回 xml 且字段名为 user_id",
                  content_type="documentation", source_url="foo-v1.0.md",
                  tags=["signal:docs"], signal="docs"),
        DraftItem(title="Foo", content="新版接口返回 json 且字段名为 accountId",
                  content_type="documentation", source_url="foo-v1.1.md",
                  tags=["signal:docs"], signal="docs"),
    ]
    gate = GateResult(
        hold_sources=set(),
        conflicts=[
            ConflictDraft(
                conflict_type="version",
                left_source="foo-v1.0.md", right_source="foo-v1.1.md",
                reason="版本家族", hold_sources=["foo-v1.0.md", "foo-v1.1.md"],
                recommended="keep_peer", recommended_reason="倾向仍有效侧",
            ),
            ConflictDraft(
                conflict_type="incoherent",
                left_source="foo-v1.0.md", right_source="foo-v1.1.md",
                reason="同桶近似", hold_sources=[],
                recommended="coexist", recommended_reason="待宿主裁决",
            ),
        ],
    )
    runtime = _QueueFakeRuntime(
        item_rows=[{"id": "item-1", "source_url": "foo-v1.0.md"}],
        conflict_rows=[
            {"id": "c-version", "item_id": "item-1",
             "user_rule_path": "foo-v1.1.md", "conflict_type": "version",
             "resolution_note": "recommended:keep_peer"},
            {"id": "c-incoh", "item_id": "item-1",
             "user_rule_path": "foo-v1.1.md", "conflict_type": "incoherent",
             "resolution_note": "recommended:coexist"},
        ],
    )
    queue_path, unresolved = await _write_host_judge_queue(
        runtime, tmp_path / ".rsi", "p1", "run1", gate, drafts,
    )
    assert unresolved == 2
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    items = {item["conflict_id"]: item for item in data["items"]}
    assert items["c-version"]["conflict_type"] == "version"
    assert items["c-version"]["recommended"] == "keep_peer"
    assert items["c-incoh"]["conflict_type"] == "incoherent"
    assert items["c-incoh"]["recommended"] == "coexist"


async def test_write_host_judge_queue_reuses_source_mapping(tmp_path, monkeypatch):
    from rsi_boot.cli.bootstrap_command import _write_host_judge_queue
    from rsi_boot.scanner.conflict_gate import ConflictDraft, DraftItem, GateResult

    calls = {"n": 0}

    async def boom(*_a, **_k):
        calls["n"] += 1
        return {"foo-v1.0.md": "item-1"}

    monkeypatch.setattr("rsi_boot.cli.bootstrap_command._source_to_item_id", boom)
    drafts = [
        DraftItem(title="Foo", content="旧版", content_type="documentation",
                  source_url="foo-v1.0.md", tags=["signal:docs"], signal="docs"),
        DraftItem(title="Foo", content="新版", content_type="documentation",
                  source_url="foo-v1.1.md", tags=["signal:docs"], signal="docs"),
    ]
    gate = GateResult(
        hold_sources=set(),
        conflicts=[
            ConflictDraft(
                conflict_type="version",
                left_source="foo-v1.0.md", right_source="foo-v1.1.md",
                reason="版本家族", hold_sources=["foo-v1.0.md", "foo-v1.1.md"],
                recommended="keep_peer", recommended_reason="倾向仍有效侧",
            ),
        ],
    )
    runtime = _QueueFakeRuntime(
        item_rows=[{"id": "item-1", "source_url": "foo-v1.0.md"}],
        conflict_rows=[{
            "id": "c-version", "item_id": "item-1",
            "user_rule_path": "foo-v1.1.md", "conflict_type": "version",
            "resolution_note": "recommended:keep_peer",
        }],
    )
    queue_path, unresolved = await _write_host_judge_queue(
        runtime, tmp_path / ".rsi", "p1", "run1", gate, drafts,
        source_to_item_id={"foo-v1.0.md": "item-1"},
    )
    assert unresolved == 1
    assert calls["n"] == 0
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    assert data["items"][0]["conflict_id"] == "c-version"


async def test_write_host_judge_queue_pages_beyond_list_limit(tmp_path):
    from rsi_boot.cli.bootstrap_command import _write_host_judge_queue
    from rsi_boot.scanner.conflict_gate import GateResult

    n = 201
    runtime = _QueueFakeRuntime(
        item_rows=[{"id": "item-1", "source_url": "foo-v1.0.md"}],
        conflict_rows=[
            {
                "id": f"c{i}", "item_id": "item-1",
                "user_rule_path": f"peer-{i}.md", "conflict_type": "incoherent",
                "resolution_note": "recommended:coexist",
            }
            for i in range(n)
        ],
    )
    queue_path, unresolved = await _write_host_judge_queue(
        runtime, tmp_path / ".rsi", "p1", "run1",
        GateResult(hold_sources=set(), conflicts=[]), [],
        source_to_item_id={"foo-v1.0.md": "item-1"},
    )
    assert unresolved == n
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(data["items"]) == n
