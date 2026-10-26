"""rsi_web_ui：快照口径，以及 open 之后才能访问、close 之后端口不再应答。"""

import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest
import yaml

from rsi_boot.api.tools import web_ui_tool
from rsi_boot.dashboard.server import close_dashboard, open_dashboard
from rsi_boot.dashboard.snapshot import build_snapshot


@pytest.fixture(autouse=True)
def _dashboard_down():
    close_dashboard()
    yield
    close_dashboard()


def _get(url: str):
    last = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status, resp.read()
        except Exception as exc:
            last = exc
            time.sleep(0.05)
    raise last


def _mem(rsi, rel: str, **fields) -> None:
    path = rsi / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(fields, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_snapshot_counts_dirs_events_and_gaps(tmp_path):
    rsi = tmp_path / ".rsi"
    kept = "a" * 32
    gone = "b" * 32
    _mem(
        rsi, "memory/conventions/naming.yaml",
        id=kept, type="convention", title="用 snake_case", domain="code", status="active",
    )
    _mem(
        rsi, "memory/gene-map/manual/block.yaml",
        id="c" * 32, type="gene_case", title="空块", domain="parser",
    )
    _mem(
        rsi, "memory/pending/prohibitions/no-star.yaml",
        id="d" * 32, type="prohibition", title="不要 SELECT *", domain="sql",
        updated_at="2026-10-13T00:00:00Z",
    )
    _mem(
        rsi, "memory/archive/conventions/old.yaml",
        id="e" * 32, type="convention", title="旧约定",
    )
    logs = rsi / "logs"
    logs.mkdir(parents=True)
    day0 = "2026-10-01T01:00:00Z"
    day2 = "2026-10-03T01:00:00Z"
    lines = [
        {"ts": day0, "kind": "recall", "task": "审查命名", "retrieved": [kept, gone]},
        {"ts": day2, "kind": "recall", "task": "再看一眼", "retrieved": [kept]},
        {"ts": day2, "kind": "feedback", "action": "accepted"},
        {"ts": day2, "kind": "feedback", "action": "ignored"},
        "not-json",
    ]
    (logs / "events.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) if isinstance(row, dict) else row for row in lines) + "\n",
        encoding="utf-8",
    )
    older = datetime(2026, 9, 30, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (logs / "events-202609.jsonl").write_text(
        json.dumps({"ts": older, "kind": "feedback", "action": "rejected"}) + "\n",
        encoding="utf-8",
    )

    snap = build_snapshot(rsi)
    assert snap["meta"]["memTotal"] == 4
    assert snap["meta"]["pendingReview"] == 1
    assert snap["meta"]["healthPct"] == 50.0  # conventions + gene-map；pending / archive 不是 active
    cats = {row["label"]: row["value"] for row in snap["memory"]["categories"]}
    assert cats["conventions"] == 2  # 正式 + archive 仍算 conventions
    assert cats["gene-map"] == 1
    assert cats["prohibitions"] == 1
    assert snap["pendingItems"][0]["title"] == "不要 SELECT *"
    assert snap["feedback"]["total"] == 3
    assert snap["feedback"]["adopted"] == 1
    assert snap["feedback"]["breakdown"]["rejected"] == 1
    assert snap["recallStats"]["events"] == 2
    assert snap["recallStats"]["hits"] == 3
    assert snap["recallStats"]["unmappedHits"] == 1
    assert snap["series"][0]["date"] == "2026-09-30"
    assert snap["series"][1]["date"] == "2026-10-01"
    assert snap["series"][2] == {
        "date": "2026-10-02", "recall": 0, "hits": 0, "feedback": 0,
        "events": 0, "fb_accepted": 0, "fb_total": 0,
    }
    assert any(row["kind"] == "recall" for row in snap["recent"])
    assert all("ignored" not in row["text"] for row in snap["recent"])


def test_snapshot_missing_rsi_does_not_create_it(tmp_path):
    target = tmp_path / ".rsi"
    snap = build_snapshot(target)
    assert snap["meta"]["memTotal"] == 0
    assert snap["series"] == []
    assert snap["feedback"]["adoptionPct"] == 0
    assert not target.exists()


def test_open_serves_dashboard_close_refuses(tmp_path):
    rsi = tmp_path / ".rsi"
    _mem(rsi, "memory/conventions/a.yaml", id="a" * 32, type="convention", title="一条", domain="code")
    info = open_dashboard(rsi, port=0)
    try:
        assert info["listening"] is True
        assert info["url"].endswith("/dashboard")
        status, page = _get(info["url"])
        assert status == 200
        assert "项目记忆数据大屏".encode() in page
        assert b"/api/snapshot" in page
        status, body = _get(info["snapshot_url"])
        assert status == 200
        snap = json.loads(body)
        assert snap["meta"]["memTotal"] == 1
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(info["url"].rsplit("/", 1)[0] + "/nope", timeout=2)
        assert err.value.code == 404
        again = open_dashboard(rsi, port=0)
        assert again["url"] == info["url"]
    finally:
        url = info["url"]
        closed = close_dashboard()
    assert closed["listening"] is False
    deadline = time.time() + 3
    refused = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
        except Exception:
            refused = True
            break
        time.sleep(0.05)
    assert refused


def test_busy_port_moves_to_the_next_one(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    busy = sock.getsockname()[1]
    try:
        info = open_dashboard(tmp_path / ".rsi", port=busy)
        got = int(info["url"].split(":")[-1].split("/")[0])
        assert got != busy
        status, _ = _get(info["url"])
        assert status == 200
    finally:
        close_dashboard()
        sock.close()


class _Runtime:
    def __init__(self, root):
        self.store = type("Store", (), {"rsi_dir": root / ".rsi"})()


async def test_tool_open_status_close(tmp_path, monkeypatch):
    monkeypatch.setattr("rsi_boot.dashboard.server.DEFAULT_PORT", 0)
    runtime = _Runtime(tmp_path)
    opened = await web_ui_tool.handle(runtime, {"action": "open", "lang": "zh"})
    try:
        assert opened["status"] == "success"
        assert opened["data"]["listening"] is True
        assert "/dashboard" in opened["data"]["url"]
        assert "记忆大屏已开启" in opened["message"]
        status = await web_ui_tool.handle(runtime, {"action": "status", "lang": "en"})
        assert status["data"]["url"] == opened["data"]["url"]
        assert "listening" in status["message"]
    finally:
        closed = await web_ui_tool.handle(runtime, {"action": "close", "lang": "zh"})
    assert closed["data"]["listening"] is False
    idle = await web_ui_tool.handle(runtime, {"action": "status", "lang": "zh"})
    assert idle["data"]["listening"] is False
    bad = await web_ui_tool.handle(runtime, {"action": "restart", "lang": "zh"})
    assert bad["status"] == "error"
    assert bad["code"] == "invalid"
