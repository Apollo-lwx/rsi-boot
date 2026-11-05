"""基线适配器统一接口。

每个适配器实现同一协议（同步方法，内部可自行管理事件循环）：

    adapter = SomeAdapter()
    adapter.open(work_dir)            # 每个数据集一次
    for sample in samples:
        adapter.reset()               # 每个对话样本一次（清空记忆）
        adapter.ingest(rows)          # rows = [(gold_key, title, content)]
        for q in sample.questions:
            keys = adapter.retrieve(q.question, top_k=10)   # 返回 gold_key 列表
    adapter.close()

适配器直接返回 gold_key（LoCoMo 的 dia_id / LongMemEval 的 session id），
评测器不再需要 doc_id -> gold 投影。
"""

from __future__ import annotations

from pathlib import Path


class BaseAdapter:
    """接口约定与公共工具。子类必须实现 open/reset/ingest/retrieve/close。"""

    name = "base"

    def open(self, work: Path) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def ingest(self, rows: list[tuple[str, str, str]]) -> None:
        raise NotImplementedError

    def retrieve(self, query: str, top_k: int) -> list[str]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # 结果元信息（如 vanilla-rag 的 embedding 来源），写进榜尾注
    def notes(self) -> dict:
        return {}
