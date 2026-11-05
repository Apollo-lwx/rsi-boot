"""RSI 本体适配器：走产品召回（RecallService，冷启动 recall-balanced）。

与 benchmark/recall_board.py 同一条链路，保证主榜数字与对比表严格可比。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.baselines import BaseAdapter
from benchmark.recall_board import _install_list_cache, _project, _write_docs


class RsiAdapter(BaseAdapter):
    name = "rsi"

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime = None
        self._work: Path | None = None
        self._doc_to_gold: dict[str, str] = {}

    def open(self, work: Path) -> None:
        from rsi_boot.bootstrap import build_runtime

        self._work = work
        self._loop = asyncio.new_event_loop()
        self._runtime = self._loop.run_until_complete(
            build_runtime(project_root=work)
        )
        _install_list_cache(self._runtime.store)

    def reset(self) -> None:
        assert self._runtime is not None
        self._doc_to_gold = {}

    def ingest(self, rows: list[tuple[str, str, str]]) -> None:
        assert self._runtime is not None and self._work is not None
        self._doc_to_gold = _write_docs(self._work / ".rsi", rows)
        # 写入后让 list 缓存与召回索引失效，等价于 recall_board 的处理
        self._runtime.store._bench_gen += 1  # type: ignore[attr-defined]
        self._runtime.recall._index = None
        self._runtime.recall._index_key = None

    def retrieve(self, query: str, top_k: int) -> list[str]:
        assert self._runtime is not None and self._loop is not None

        async def _go() -> list[str]:
            payload = await self._runtime.recall.recall(
                query, self._runtime.project_id, user_id="bench", top_k=top_k,
            )
            return [item["id"] for item in payload.get("items") or []]

        ranked = self._loop.run_until_complete(_go())
        return _project(ranked, self._doc_to_gold)

    def close(self) -> None:
        if self._runtime is not None and self._loop is not None:
            self._loop.run_until_complete(self._runtime.close())
        if self._loop is not None:
            self._loop.close()
        self._runtime = None
        self._loop = None

    def notes(self) -> dict:
        return {"pipeline": "RecallService recall-balanced, cold start"}
