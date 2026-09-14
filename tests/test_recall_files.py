import pytest


@pytest.mark.asyncio
async def test_recall_logs_document_ids(tmp_path):
    from rsi_boot.memory.logstore import iter_events
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.recall_service import RecallService

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须写列名"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml",
    )
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="查询列清单", content="别把所有列一次查出来"),
        dest=official_dir(store.rsi_dir, "convention") / "cols--bbbbbbbb.yaml",
    )
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="列名", project_id="local")
    assert set(payload) >= {
        "prohibitions", "items", "skills", "feedback_token", "recall_arm",
        "gene_cases", "teaching_cases", "episodes",
    }
    assert "hint" in payload
    row = list(iter_events(tmp_path / ".rsi"))[-1]
    assert row["retrieved"] and all(len(x) == 32 and int(x, 16) >= 0 for x in row["retrieved"])
    assert isinstance(payload["skills"], list)


@pytest.mark.asyncio
async def test_store_recall_does_not_apply_cosine_gate_to_raw_idf(tmp_path, monkeypatch):
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.recall_service import RecallService

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="b" * 32, type="convention", title="查询列清单", content="别把所有列一次查出来"),
        dest=official_dir(store.rsi_dir, "convention") / "cols--bbbbbbbb.yaml",
    )

    def fake_search(index, query, *, types=None, top_n=5):
        if types and "convention" in types:
            return [("b" * 32, 0.1)]
        return []

    monkeypatch.setattr("rsi_boot.rag.index.search", fake_search)
    payload = await RecallService(store=store, feedback_secret="test").recall("列名", "local")
    assert any(item["id"] == "b" * 32 for item in payload["items"])
