"""LLM 查询分解（RSI_ENABLE=decompose 实验特性）测试。

覆盖：输出解析容错、端点未配置/调用失败的 fail-open、磁盘缓存（复跑零 token）、
RecallService 接线（子查询救回词面无命中文档）、默认关门控（逐位一致）。
真实 LLM 不进 CI——端到端用 monkeypatch 假的 decompose_async / _call_llm。
"""

from __future__ import annotations

import json
import uuid

import pytest

from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag import decompose as dc
from rsi_boot.services.recall_service import RecallService


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in ("RSI_ENABLE", "RSI_DECOMPOSE_BASE_URL", "RSI_DECOMPOSE_API_KEY",
                "RSI_DECOMPOSE_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RSI_DECOMPOSE_CACHE", str(tmp_path / "decompose.json"))
    dc._memo = {}
    dc._memo_path = None
    yield


def _write(store: MemoryStore, *, title: str, content: str, type: str = "convention") -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, type) / memory_filename(title, item_id)
    store.write(MemoryDoc(id=item_id, type=type, title=title, content=content), dest=dest)
    return item_id


# ---------- 解析 ----------


class TestParse:
    Q = "When did Caroline's trip happen?"

    def test_json_array(self):
        out = dc._parse_subqueries('["Where did Caroline go?", "When is the festival?"]', self.Q)
        assert out == ["Where did Caroline go?", "When is the festival?"]

    def test_prose_wrapped_json(self):
        out = dc._parse_subqueries('Sure! Here you go:\n["A?", "B?"]\nHope that helps.', self.Q)
        assert out == ["A?", "B?"]

    def test_line_fallback(self):
        out = dc._parse_subqueries("1. Where did Caroline go?\n2. When is the festival?", self.Q)
        assert "Where did Caroline go?" in out

    def test_drops_original_and_caps_three(self):
        out = dc._parse_subqueries(
            json.dumps([self.Q, "s1", "s2", "s3", "s4"]), self.Q,
        )
        assert self.Q not in out
        assert out == ["s1", "s2", "s3"]


# ---------- fail-open 与缓存 ----------


class TestDecompose:
    def test_no_endpoint_returns_empty(self, tmp_path):
        assert dc.decompose("What happened?", tmp_path) == []

    def test_llm_failure_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RSI_DECOMPOSE_BASE_URL", "http://localhost:1")
        monkeypatch.setenv("RSI_DECOMPOSE_API_KEY", "k")

        def _boom(question, base, key, model):
            raise RuntimeError("network down")

        monkeypatch.setattr(dc, "_call_llm", _boom)
        assert dc.decompose("What happened?", tmp_path) == []

    def test_cache_roundtrip_zero_token_rerun(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RSI_DECOMPOSE_BASE_URL", "http://relay")
        monkeypatch.setenv("RSI_DECOMPOSE_API_KEY", "k")
        monkeypatch.setenv("RSI_DECOMPOSE_MODEL", "cheap-model")
        calls = []

        def _fake(question, base, key, model):
            calls.append(question)
            return '["sub one?", "sub two?"]'

        monkeypatch.setattr(dc, "_call_llm", _fake)
        assert dc.decompose("Q?", tmp_path) == ["sub one?", "sub two?"]
        assert dc.decompose("Q?", tmp_path) == ["sub one?", "sub two?"]
        assert len(calls) == 1  # 第二次走缓存

        # 模拟进程重启：清空内存缓存，从磁盘恢复，仍零 LLM 调用
        dc._memo = {}
        dc._memo_path = None
        assert dc.decompose("Q?", tmp_path) == ["sub one?", "sub two?"]
        assert len(calls) == 1

    def test_model_change_invalidates_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RSI_DECOMPOSE_BASE_URL", "http://relay")
        monkeypatch.setenv("RSI_DECOMPOSE_API_KEY", "k")
        monkeypatch.setenv("RSI_DECOMPOSE_MODEL", "m1")
        monkeypatch.setattr(dc, "_call_llm", lambda q, b, k, m: '["a"]')
        dc.decompose("Q?", tmp_path)
        monkeypatch.setenv("RSI_DECOMPOSE_MODEL", "m2")
        dc._memo = {}
        dc._memo_path = None
        dc.decompose("Q?", tmp_path)
        # 两个模型的缓存键并存
        assert len(json.loads((tmp_path / "decompose.json").read_text(encoding="utf-8"))) == 2


# ---------- RecallService 接线与门控 ----------


class TestRecallWiring:
    async def test_default_off_not_consulted(self, store, monkeypatch):
        _write(store, title="pytest basetemp", content="pytest basetemp 不得放在被测仓库内。")

        async def _boom(task, rsi_dir):
            raise AssertionError("decompose should not be consulted")

        monkeypatch.setattr(dc, "decompose_async", _boom)
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("pytest basetemp 放哪", "p1", user_id="t")
        assert payload["items"]

    async def test_subquery_rescues_lexical_miss(self, store, monkeypatch):
        """原问题词面无命中的文档，由 LLM 子查询救回"""
        _write(store, title="Caroline trip",
               content="Caroline went to the lavender festival in Provence.")
        second = _write(store, title="Festival dates",
                        content="The lavender festival in Provence happens in July.")
        monkeypatch.setenv("RSI_ENABLE", "decompose")

        async def _fake(task, rsi_dir):
            return ["When is the lavender festival in Provence?"]

        monkeypatch.setattr(dc, "decompose_async", _fake)
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("When did Caroline's trip happen?", "p1",
                                   user_id="t", top_k=5)
        ids = [row["id"] for row in payload["items"]]
        assert second in ids

    async def test_decompose_failure_silent(self, store, monkeypatch):
        _write(store, title="audit finish", content="未 audit_finish 前不得改业务代码。")
        monkeypatch.setenv("RSI_ENABLE", "decompose")

        async def _boom(task, rsi_dir):
            raise RuntimeError("relay down")

        monkeypatch.setattr(dc, "decompose_async", _boom)
        svc = RecallService(store=store, feedback_secret="s")
        # decompose_async 炸在 await 点会直接冒泡——产品语义是 fail-open，
        # 接线处必须兜底
        payload = await svc.recall("audit_finish 之前能改代码吗", "p1", user_id="t")
        assert payload["items"]
