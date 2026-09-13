from pathlib import Path

import pytest

from rsi_boot.api.tools.knowledge_review_tool import TOOL_DESCRIPTION
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.ux.messages import TOOL_DESC, t


@pytest.mark.asyncio
async def test_review_approve_moves_and_injects(tmp_path):
    from rsi_boot.memory.paths import official_dir, pending_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.knowledge_service import KnowledgeService

    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段")
    store.write(doc, dest=pending_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    out = await svc.review_approve("a"*32)
    assert store.read("a"*32).status == "active"
    assert official_dir(store.rsi_dir, "prohibition") in Path(out["moved"][0]["to"]).parents or "prohibitions" in out["moved"][0]["to"]
    assert list((tmp_path / ".cursor" / "rules").glob("rsi-prohibition-*.mdc"))


@pytest.mark.asyncio
async def test_review_reject_archives(tmp_path):
    from rsi_boot.memory.paths import pending_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.knowledge_service import KnowledgeService

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="x", content="y"),
        dest=pending_dir(store.rsi_dir, "convention") / "x--bbbbbbbb.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    await svc.review_reject("b"*32)
    got = store.read("b"*32)
    assert got.status == "archived"
    assert got.extra.get("review") == "rejected"


@pytest.mark.asyncio
async def test_add_norm_goes_pending_doc_goes_official(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    prohibition = await svc.add(KnowledgeItem(
        project_id="p", title="禁止 SELECT *", content="必须列字段", content_type="prohibition",
    ))
    doc = await svc.add(KnowledgeItem(
        project_id="p", title="接口说明", content="文档正文", content_type="documentation",
    ))
    assert set(prohibition) >= {"id", "path", "message"}
    assert str(prohibition["path"]).replace("\\", "/").startswith("memory/pending/prohibitions")
    assert str(doc["path"]).replace("\\", "/").startswith("memory/documentation")
    assert store.read(prohibition["id"]).status == "pending_review"
    assert store.read(doc["id"]).status == "active"


@pytest.mark.asyncio
async def test_delete_moves_to_archive(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    dest = official_dir(store.rsi_dir, "convention") / "x--cccccccc.yaml"
    store.write(MemoryDoc(id="c"*32, type="convention", title="x", content="y"), dest=dest)
    svc = KnowledgeService(store=store, project_root=tmp_path)
    assert await svc.delete("c"*32, "p") is True
    got = store.read("c"*32)
    assert got.status == "archived"
    assert "archive" in (got.path or "").replace("\\", "/")


@pytest.mark.asyncio
async def test_search_uses_expand_query_not_exact_title(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="d"*32, type="prohibition", title="禁止 SELECT *", content="查询必须显式列字段"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--dddddddd.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    items = await svc.search("别把所有列一次查出来", "p")
    assert any(it.id.hex == "d" * 32 for it in items)


@pytest.mark.asyncio
async def test_review_approve_skill_writes_skill_yaml(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(
            id="e"*32, type="skill", title="Gateway Parse", content="body",
            payload={"name": "gateway-parser"},
        ),
        dest=pending_dir(store.rsi_dir, "skill") / "gateway--eeeeeeee.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    out = await svc.review_approve("e"*32)
    dest = official_dir(store.rsi_dir, "skill") / "gateway-parser" / "skill.yaml"
    assert dest.is_file()
    assert "skill.yaml" in out["moved"][0]["to"].replace("\\", "/")
    assert store.read("e"*32).status == "active"


@pytest.mark.asyncio
async def test_review_messages_use_locked_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "en")
    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="f"*32, type="prohibition", title="t", content="c"),
        dest=pending_dir(store.rsi_dir, "prohibition") / "t--ffffffff.yaml",
    )
    store.write(
        MemoryDoc(id="1"*32, type="convention", title="u", content="v"),
        dest=pending_dir(store.rsi_dir, "convention") / "u--11111111.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    approved = await svc.review_approve("f"*32)
    rejected = await svc.review_reject("1"*32)
    assert approved["message"] == t("REVIEW_APPROVED", "en", n=1)
    assert rejected["message"] == t("REVIEW_REJECTED", "en", n=1)


def test_knowledge_review_tool_description_is_locked():
    assert TOOL_DESCRIPTION == TOOL_DESC["review"]
    assert "approve" in TOOL_DESCRIPTION
    assert "pending" in TOOL_DESCRIPTION


@pytest.mark.asyncio
async def test_review_official_active_reject_returns_none(tmp_path):
    from rsi_boot.injector.rule_injector import RuleInjector

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="9"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--99999999.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    await RuleInjector(store=store, project_root=tmp_path).rewrite("")
    rules = tmp_path / ".cursor" / "rules"
    before = {p.name: p.read_text(encoding="utf-8") for p in rules.glob("rsi-prohibition-*.mdc")}
    assert before
    result = await svc.review("9"*32, "p", approve=False)
    assert result is None
    after = {p.name: p.read_text(encoding="utf-8") for p in rules.glob("rsi-prohibition-*.mdc")}
    assert after == before
    assert store.read("9"*32).status == "active"


@pytest.mark.asyncio
async def test_review_pending_reject_archives_via_review(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="8"*32, type="convention", title="x", content="y"),
        dest=pending_dir(store.rsi_dir, "convention") / "x--88888888.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    result = await svc.review("8"*32, "p", approve=False)
    assert result == "archived"
    got = store.read("8"*32)
    assert got.status == "archived"
    assert got.extra.get("review") == "rejected"
