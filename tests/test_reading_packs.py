"""Reading-pack index / fingerprint IO (host-distill-knowledge Wave A Task 2)."""

from __future__ import annotations

from pathlib import Path

import yaml

from rsi_boot.scanner.reading_packs import (
    PACKS_DIRNAME,
    load_index,
    load_pack,
    pack_fingerprint,
    packs_dir,
    set_pack_status,
    source_fingerprint,
    unlink_host_judge_queue,
    write_packs,
)


def _src(**kwargs) -> dict:
    base = {"kind": "docs", "path": "docs/auth.md", "heading": "认证"}
    base.update(kwargs)
    return base


def _pack(pack_id: str = "auth", *, sources: list[dict] | None = None, **kwargs) -> dict:
    item = {
        "id": pack_id,
        "domain": "auth",
        "title": "认证与会话",
        "sources": sources if sources is not None else [_src()],
    }
    item.update(kwargs)
    return item


def test_write_packs_creates_index_yaml(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    n = write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    assert n == 1
    index_path = rsi_dir / "state" / PACKS_DIRNAME / "index.yaml"
    assert index_path.is_file()
    data = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    assert data["bootstrap_run_id"] == "run-1"
    assert data["packs"][0]["id"] == "auth"
    assert data["packs"][0]["status"] == "pending"
    assert data["packs"][0]["source_count"] == 1
    assert data["packs"][0]["fingerprint"]
    pack = load_pack(rsi_dir, "auth")
    assert pack is not None
    assert pack["id"] == "auth"
    assert pack["sources"][0]["kind"] == "docs"
    assert "content" not in pack["sources"][0]


def test_same_fingerprint_keeps_done(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[_pack()])
    index = load_index(rsi_dir)
    assert index["packs"][0]["status"] == "done"
    assert load_pack(rsi_dir, "auth")["status"] == "done"


def test_changed_source_fingerprint_resets_done_to_pending(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    changed = _pack(sources=[_src(heading="会话")])
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[changed])
    assert load_index(rsi_dir)["packs"][0]["status"] == "pending"
    assert load_pack(rsi_dir, "auth")["status"] == "pending"


def test_force_resets_done_to_pending(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[_pack()], force=True)
    assert load_index(rsi_dir)["packs"][0]["status"] == "pending"
    assert load_pack(rsi_dir, "auth")["status"] == "pending"


def test_set_pack_status_unknown_id_is_not_found(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    result = set_pack_status(rsi_dir, "missing", "done")
    assert result == {"status": "error", "code": "not_found"}


def test_set_pack_status_same_terminal_is_idempotent(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    first = set_pack_status(rsi_dir, "auth", "done", reason="ok")
    assert first["status"] != "error"
    second = set_pack_status(rsi_dir, "auth", "done")
    assert second["status"] != "error"
    assert load_pack(rsi_dir, "auth")["status"] == "done"


def test_unlink_host_judge_queue_deletes_file(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    rsi_dir.mkdir()
    queue = rsi_dir / "host_judge_queue.json"
    queue.write_text("[]", encoding="utf-8")
    unlink_host_judge_queue(rsi_dir)
    assert not queue.exists()
    unlink_host_judge_queue(rsi_dir)


def test_load_index_missing_dir_is_empty(tmp_path: Path):
    assert load_index(tmp_path / ".rsi") == {"packs": []}


def test_source_and_pack_fingerprint_are_stable_sha256():
    a = source_fingerprint("docs", path="docs/a.md", heading="A")
    b = source_fingerprint("docs", path="docs/a.md", heading="B")
    assert len(a) == 64
    assert a != b
    sources = [
        {"kind": "docs", "path": "docs/b.md", "heading": "B"},
        {"kind": "docs", "path": "docs/a.md", "heading": "A"},
    ]
    assert pack_fingerprint(sources) == pack_fingerprint(list(reversed(sources)))
    assert pack_fingerprint(sources) != pack_fingerprint(sources[:1])


def test_packs_dir_is_under_state(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    assert packs_dir(rsi_dir) == rsi_dir / "state" / "reading-packs"


def test_write_packs_does_not_accept_omitted_sources_kw(tmp_path: Path):
    import inspect

    sig = inspect.signature(write_packs)
    assert "omitted_sources" not in sig.parameters
    assert "omitted" not in sig.parameters
