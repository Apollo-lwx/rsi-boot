"""Full-context 上界参照：不做检索，按写入（时间）顺序返回全部 gold_key。

它回答「如果模型能看到全部上下文，检索这一步是不是瓶颈」。
Hit@5 对它无意义（前 5 条按时间序），所以榜里主要看它 Recall@10 恒为 1 时
其它系统的差距——该列只作参照，不参与胜负判定。
"""

from __future__ import annotations

from pathlib import Path

from benchmark.baselines import BaseAdapter


class FullContextAdapter(BaseAdapter):
    name = "full-context"

    def __init__(self) -> None:
        self._keys: list[str] = []

    def open(self, work: Path) -> None:
        self._keys = []

    def reset(self) -> None:
        self._keys = []

    def ingest(self, rows: list[tuple[str, str, str]]) -> None:
        self._keys = [gold for gold, _title, _content in rows]

    def retrieve(self, query: str, top_k: int) -> list[str]:
        return list(self._keys[:top_k])

    def notes(self) -> dict:
        return {"role": "upper-bound reference, no retrieval"}
