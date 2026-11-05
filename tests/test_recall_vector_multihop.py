"""向量融合与多跳级联（RSI_ENABLE=vector,multihop 实验特性）测试。

覆盖：融合函数单元行为、enable 门控（默认关时召回结果逐位一致）、
向量通道 fail-open（无 fastembed / 坏模型不阻塞召回）、多跳桥接词抽取。
真实 fastembed 不进 CI——端到端用注入的假 VectorIndex 验证接线。
"""

from __future__ import annotations

import uuid

import pytest

from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services import recall_service as rs
from rsi_boot.services.recall_service import (
    _MULTIHOP_DISCOUNT,
    _VECT_MIN_COS,
    _bridge_terms,
    _fuse_vector,
    _merge_hop,
    RecallService,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RSI_ENABLE", raising=False)
    monkeypatch.delenv("RSI_ABLATE", raising=False)
    monkeypatch.delenv("RSI_VECTOR_MODEL", raising=False)
    monkeypatch.delenv("RSI_VECTOR_CACHE", raising=False)
    yield


def _write(store: MemoryStore, *, title: str, content: str, type: str = "convention") -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(MemoryDoc(id=item_id, type=type, title=title, content=content), dest=dest)
    return item_id


class _FakeVectorIndex:
    """注入用的假向量索引：预设 doc_id -> cosine，不依赖 fastembed"""

    def __init__(self, cosines: dict[str, float]):
        self._cosines = cosines
        self.closed = False

    def query(self, docs, query, *, top_k=20):
        return dict(self._cosines)

    def close(self):
        self.closed = True


# ---------- _fuse_vector 单元 ----------


class TestFuseVector:
    DT = {"a": "convention", "b": "convention", "c": "convention", "d": "documentation"}

    def test_dual_hit_promoted(self):
        # b 双通道命中：1/62(词面) + 1/61(向量) > a 纯词面 1/61
        out = _fuse_vector([("a", 1.0), ("b", 0.5)], {"b": 0.9}, self.DT, {"convention"}, 0.0, 5)
        assert [d for d, _ in out][:2] == ["b", "a"]

    def test_rescue_above_threshold_only(self):
        vec = {"c": _VECT_MIN_COS + 0.01, "d": _VECT_MIN_COS - 0.01}
        out = _fuse_vector([("a", 1.0)], vec, self.DT, {"convention", "documentation"}, 0.0, 5)
        ids = [d for d, _ in out]
        assert "c" in ids and "d" not in ids

    def test_rescue_respects_channel_types(self):
        out = _fuse_vector([], {"d": 0.99}, self.DT, {"convention"}, 0.0, 5)
        assert out == []  # d 是 documentation，不进 convention 通道

    def test_cold_start_vector_only(self):
        out = _fuse_vector([], {"c": 0.9, "d": 0.8}, self.DT, {"convention", "documentation"},
                           float("-inf"), 5)
        assert [d for d, _ in out] == ["c", "d"]  # 按 cos 排序入场

    def test_truncates_to_limit(self):
        vec = {"c": 0.9, "d": 0.8}
        out = _fuse_vector([("a", 1.0), ("b", 0.9)], vec, self.DT,
                           {"convention", "documentation"}, 0.0, 2)
        assert len(out) == 2

    def test_empty_vec_is_noop(self):
        ranked = [("a", 1.0)]
        assert _fuse_vector(ranked, {}, self.DT, {"convention"}, 0.0, 5) == ranked


# ---------- _merge_hop / _bridge_terms 单元 ----------


class TestMergeHop:
    DT = {"a": "convention", "x": "convention", "y": "teaching_case"}

    def test_second_hop_enters_discounted(self):
        ranked = [("a", 1.0)]
        hop = [("a", 9.0), ("x", 8.0)]
        out = _merge_hop(ranked, hop, self.DT, {"convention"}, floor=-0.6, best=1.0, limit=5)
        ids = [d for d, _ in out]
        assert "x" in ids  # 已在榜的 a 不重复入场
        score = dict(out)["x"]
        # max_hop 含已在榜的 a=9.0：floor + span·0.5·(8/9)
        assert score == pytest.approx(-0.6 + 1.6 * _MULTIHOP_DISCOUNT * (8.0 / 9.0))

    def test_type_filter(self):
        out = _merge_hop([], [("y", 5.0)], self.DT, {"convention"}, -0.6, 1.0, 5)
        assert out == []

    def test_no_floor_no_entry(self):
        out = _merge_hop([], [("x", 5.0)], self.DT, {"convention"},
                         float("-inf"), float("-inf"), 5)
        assert out == []


class TestBridgeTerms:
    D1 = "d1" * 16
    D2 = "d2".replace("d2", "e3") * 16  # 32 位 hex

    def test_picks_high_idf_terms_not_in_query(self):
        from rsi_boot.rag.index import build_index

        docs = [
            MemoryDoc(id=self.D1, type="convention", title="Caroline",
                      content="Caroline visited the lavender festival in Provence."),
            MemoryDoc(id=self.D2, type="convention", title="Other",
                      content="Completely unrelated notes about shopping lists."),
        ]
        index = build_index(docs)
        terms = _bridge_terms(index, "Where did Caroline go?", [self.D1])
        assert terms
        assert "caroline" not in terms  # 查询词不当桥接词
        assert any(t in terms for t in ("lavender", "festival", "provence"))

    def test_empty_seed(self):
        from rsi_boot.rag.index import build_index

        index = build_index([MemoryDoc(id=self.D1, type="convention", title="t", content="c")])
        assert _bridge_terms(index, "q", []) == []


# ---------- 门控与 fail-open ----------


class TestGating:
    async def test_default_off_byte_identical(self, store, monkeypatch):
        """不开 RSI_ENABLE 时，融合代码路径完全不介入（结果与纯词面一致）"""
        _write(store, title="pytest basetemp", content="pytest basetemp 不得放在被测仓库内。")

        def _boom(self, store, docs, query):  # 若被调用则炸——默认关时不许走到
            raise AssertionError("vector channel should not be consulted")

        monkeypatch.setattr(RecallService, "_vector_cosines", _boom)
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("pytest basetemp 放哪", "p1", user_id="t")
        assert payload["items"]  # 正常召回，未触向量通道

    async def test_vector_enabled_uses_injected_index(self, store, monkeypatch):
        """enable 后走注入的假索引：语义命中的文档被救援进榜"""
        target = _write(store, title="Melanie pottery",
                        content="Melanie paints ceramic pots every Sunday.", type="documentation")
        # 词面完全无关的查询，BM25 无命中；向量通道给出强余弦
        monkeypatch.setenv("RSI_ENABLE", "vector")
        svc = RecallService(store=store, feedback_secret="s", project_root=None)
        svc._vector_tried = True
        svc._vector_index = _FakeVectorIndex({target: 0.95})
        payload = await svc.recall("What does she glaze on weekends?", "p1", user_id="t")
        ids = [row["id"] for row in payload["items"]]
        assert target in ids

    async def test_vector_failure_is_silent(self, store, monkeypatch):
        """VectorIndex.create 失败 → fail-open，召回照常"""
        _write(store, title="audit finish", content="未 audit_finish 前不得改业务代码。")
        monkeypatch.setenv("RSI_ENABLE", "vector")
        from rsi_boot.rag.vector import VectorIndex

        monkeypatch.setattr(VectorIndex, "create", classmethod(lambda cls, rsi_dir: None))
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("audit_finish 之前能改代码吗", "p1", user_id="t")
        assert payload["items"]

    async def test_multihop_admits_second_hop(self, store, monkeypatch):
        """多跳：与查询零词面重叠但与首跳共享桥接词的文档被二跳带入"""
        first = _write(store, title="Caroline trip",
                       content="Caroline went to the lavender festival in Provence.")
        second = _write(store, title="Provence weather",
                        content="The lavender festival in Provence happens in July.")
        monkeypatch.setenv("RSI_ENABLE", "multihop")
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("When did Caroline's trip happen?", "p1", user_id="t", top_k=5)
        ids = [row["id"] for row in payload["items"]]
        assert first in ids
        assert second in ids  # 零查询词重叠，只能靠多跳桥接入场

    async def test_drain_closes_vector_index(self, store):
        svc = RecallService(store=store, feedback_secret="s")
        fake = _FakeVectorIndex({})
        svc._vector_index = fake
        await svc.drain()
        assert fake.closed and svc._vector_index is None
