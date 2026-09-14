"""rsi_memory graph returns mermaid text; does not write knowledge markdown."""

from __future__ import annotations

import pytest
import yaml

from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc


def _seed_docs(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = store.write(
        MemoryDoc(id="a" * 32, type="convention", title="约定甲", content="写法甲"),
        dest=official_dir(store.rsi_dir, "convention") / memory_filename("约定甲", "a" * 32),
    )
    b = store.write(
        MemoryDoc(id="b" * 32, type="convention", title="约定乙", content="写法乙"),
        dest=official_dir(store.rsi_dir, "convention") / memory_filename("约定乙", "b" * 32),
    )
    catalog = tmp_path / ".rsi" / "state" / "catalog.yaml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(
        yaml.safe_dump(
            {"edges": [{"from": a.id, "to": b.id, "rel": "cites"}]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return a, b


@pytest.mark.asyncio
async def test_graph_returns_mermaid_with_ids_no_md(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.memory_tool import handle
    from rsi_boot.bootstrap import build_runtime

    a, b = _seed_docs(tmp_path)
    md_before = set((tmp_path / ".rsi" / "memory").rglob("*.md"))

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "graph"})
        assert out.get("code") != "not_in_phase"
        assert out["status"] == "success"
        mermaid = (out.get("data") or {}).get("mermaid") or out.get("message") or ""
        assert a.id in mermaid
        assert b.id in mermaid
        assert "graph" in mermaid.lower() or "-->" in mermaid
        md_after = set((tmp_path / ".rsi" / "memory").rglob("*.md"))
        assert md_after == md_before
    finally:
        await rt.close()


def test_render_mermaid_quotes_digit_starting_ids(tmp_path):
    from rsi_boot.memory.graph import add_edge, render_mermaid

    from_id = "0" + "a" * 31
    to_id = "1" + "b" * 31
    rsi_dir = tmp_path / ".rsi"
    add_edge(rsi_dir, from_id, to_id, "cites")
    mermaid = render_mermaid(rsi_dir)
    assert f'"{from_id}" -->|cites| "{to_id}"' in mermaid
    assert f"  {from_id} -->|cites| {to_id}" not in mermaid
