"""向量通道产品化（P2）测试：托管嵌入后端、后台预热、init --vector。

真实 fastembed / 托管端点不进 CI——用假 embedder 与 monkeypatch 验证接线。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

import rsi_boot.__main__ as main_mod
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag import vector as vec
from rsi_boot.services.recall_service import RecallService


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in ("RSI_ENABLE", "RSI_VECTOR_MODEL", "RSI_VECTOR_BASE_URL",
                "RSI_VECTOR_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY",
                "RSI_VECTOR_STORE"):
        monkeypatch.delenv(var, raising=False)
    yield


def _write(store: MemoryStore, *, title: str, content: str) -> str:
    item_id = uuid.uuid4().hex
    dest = official_dir(store.rsi_dir, "convention") / memory_filename(title, item_id)
    store.write(MemoryDoc(id=item_id, type="convention", title=title, content=content),
                dest=dest)
    return item_id


class _FakeEmbedder:
    """确定性假嵌入：向量 = [len(text) % 7, 含 'cat' 与否, 1.0]"""

    def embed(self, texts):
        return [[float(len(t) % 7), 1.0 if "cat" in t else 0.0, 1.0] for t in texts]


def _make_index(tmp_path: Path, monkeypatch, model: str = "BAAI/bge-small-en-v1.5"):
    monkeypatch.setattr(vec, "_load_model", lambda m, c: _FakeEmbedder())
    return vec.VectorIndex(model, tmp_path / "mcache", tmp_path / "vectors.sqlite")


# ---------- 托管嵌入后端 ----------


class TestHostedBackend:
    def test_slash_model_uses_fastembed(self, tmp_path, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(vec, "_load_model", lambda m, c: sentinel)
        got = vec._load_embedder("BAAI/bge-small-en-v1.5", tmp_path)
        assert got is sentinel

    def test_hosted_model_with_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RSI_VECTOR_API_KEY", "k")
        monkeypatch.setenv("RSI_VECTOR_BASE_URL", "http://relay")

        class _FakeClient:
            def __init__(self, base_url=None, api_key=None, timeout=None):
                self.embeddings = self
                self._base = base_url

            def create(self, model, input):
                assert model == "text-embedding-3-small"
                return type("R", (), {"data": [
                    type("D", (), {"embedding": [0.1, 0.2]}) for _ in input
                ]})()

        import openai
        monkeypatch.setattr(openai, "OpenAI", _FakeClient)
        emb = vec._load_embedder("text-embedding-3-small", tmp_path)
        assert isinstance(emb, vec._HostedEmbedding)
        out = emb.embed(["hello", "world"])
        assert out == [[0.1, 0.2], [0.1, 0.2]]

    def test_hosted_without_key_falls_back_local(self, tmp_path, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(vec, "_load_model", lambda m, c: sentinel)
        got = vec._load_embedder("text-embedding-3-small", tmp_path)
        assert got is sentinel  # 无密钥回退本地（fail-open 链）


# ---------- warm / 线程安全 ----------


class TestWarm:
    def test_warm_builds_matrix_and_caches(self, tmp_path, monkeypatch):
        idx = _make_index(tmp_path, monkeypatch)
        docs = [
            MemoryDoc(id="a" * 32, type="convention", title="cat rule",
                      content="cats sleep all day"),
            MemoryDoc(id="b" * 32, type="convention", title="dog rule",
                      content="dogs bark at night"),
        ]
        idx.warm(docs)
        assert idx._matrix is not None and idx._matrix.shape == (2, 3)
        scores = idx.query(docs, "cat", top_k=5)
        assert set(scores) <= {"a" * 32, "b" * 32}
        # 磁盘缓存落盘：重建索引实例后无需重算
        idx2 = _make_index(tmp_path, monkeypatch)
        idx2.warm(docs)
        assert idx2._matrix is not None

    def test_warm_failure_silent(self, tmp_path, monkeypatch):
        idx = _make_index(tmp_path, monkeypatch)

        def _boom(texts):
            raise RuntimeError("embed boom")

        idx._emb = type("E", (), {"embed": staticmethod(_boom)})()
        idx.warm([MemoryDoc(id="a" * 32, type="convention", title="t", content="c")])
        assert idx._matrix is None  # 静默，不抛出


# ---------- RecallService 后台预热接线 ----------


class _WarmFakeIndex:
    def __init__(self):
        self.warmed_with = None
        self.closed = False

    def warm(self, docs):
        self.warmed_with = list(docs)

    def query(self, docs, query, *, top_k=20):
        return {}

    def close(self):
        self.closed = True


class TestRecallPrewarm:
    async def test_first_recall_starts_background_warm(self, store, monkeypatch):
        _write(store, title="cat facts", content="cats sleep all day long")
        monkeypatch.setenv("RSI_ENABLE", "vector")
        fake = _WarmFakeIndex()
        monkeypatch.setattr(
            vec.VectorIndex, "create",
            classmethod(lambda cls, rsi_dir: fake),
        )
        svc = RecallService(store=store, feedback_secret="s")
        payload = await svc.recall("cat facts", "p1", user_id="t")
        assert payload["items"]
        import threading, time
        for _ in range(50):  # 等后台线程跑完（fake 秒回）
            if fake.warmed_with is not None:
                break
            time.sleep(0.02)
        assert fake.warmed_with is not None and len(fake.warmed_with) == 1
        assert any(t.name == "rsi-vector-warm" for t in threading.enumerate()) \
            or fake.warmed_with is not None


# ---------- rsi init --vector ----------


class TestInitVector:
    def test_missing_fastembed_returns_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "fastembed", None)  # import 即 ImportError
        rc = main_mod._setup_vector(tmp_path)
        assert rc == 2
        assert "rsi-boot[vector]" in capsys.readouterr().err
        assert not (tmp_path / "rsi-boot.yaml").exists()  # 不半配置

    def test_writes_enable_and_idempotent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(vec, "_load_model", lambda m, c: object())
        monkeypatch.setattr(
            vec.VectorIndex, "create",
            classmethod(lambda cls, rsi_dir: _WarmFakeIndex()),
        )
        assert main_mod._setup_vector(tmp_path) == 0
        text = (tmp_path / "rsi-boot.yaml").read_text(encoding="utf-8")
        assert "vector" in text
        capsys.readouterr()
        assert main_mod._setup_vector(tmp_path) == 0  # 幂等
        assert "无需改动" in capsys.readouterr().out

    def test_preserves_existing_yaml_keys(self, tmp_path, monkeypatch):
        (tmp_path / "rsi-boot.yaml").write_text(
            "ablation:\n- temporal\n", encoding="utf-8",
        )
        monkeypatch.setattr(vec, "_load_model", lambda m, c: object())
        monkeypatch.setattr(
            vec.VectorIndex, "create",
            classmethod(lambda cls, rsi_dir: _WarmFakeIndex()),
        )
        assert main_mod._setup_vector(tmp_path) == 0
        text = (tmp_path / "rsi-boot.yaml").read_text(encoding="utf-8")
        assert "temporal" in text and "vector" in text
