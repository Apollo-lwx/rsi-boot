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
    assert set(payload) >= {"prohibitions", "items", "skills", "feedback_token", "recall_arm"}
    assert "gene_cases" not in payload
    row = list(iter_events(tmp_path / ".rsi"))[-1]
    assert row["retrieved"] and all(len(x) == 32 and int(x, 16) >= 0 for x in row["retrieved"])
    assert isinstance(payload["skills"], list)
