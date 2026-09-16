from pathlib import Path

import pytest

from rsi_boot.api.tools.knowledge_review_tool import TOOL_DESCRIPTION
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.memory.paths import official_dir, pending_dir, review_dir
from rsi_boot.memory.store import MemoryStore
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.ux.lang import locale_lang
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
    proh = store.read(prohibition)
    written = store.read(doc)
    assert (proh.path or "").replace("\\", "/").startswith("memory/pending/prohibitions")
    assert (written.path or "").replace("\\", "/").startswith("memory/documentation")
    assert proh.status == "pending_review"
    assert written.status == "active"


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
async def test_list_includes_pending_documentation(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="e" * 32, type="documentation", title="对话摘录", content="问答正文足够长"),
        dest=review_dir(store.rsi_dir, "documentation") / "faq--eeeeeeee.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    rows = await svc.list("p1")
    assert any(r["id"] == "e" * 32 and r["status"] == "pending_review" for r in rows)


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


def _yaml_count(rsi: Path) -> int:
    return len(list(rsi.rglob("*.yaml")))


@pytest.mark.asyncio
async def test_add_rejects_content_over_1500(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    before = _yaml_count(store.rsi_dir)
    result = await svc.add(KnowledgeItem(
        project_id="p", title="超长条", content="字" * 1501, content_type="documentation",
    ))
    assert result == {
        "status": "error",
        "code": "invalid",
        "message": t("KNOWLEDGE_TOO_LONG", locale_lang()),
    }
    assert _yaml_count(store.rsi_dir) == before


@pytest.mark.asyncio
async def test_add_rejects_harvest_titles(tmp_path):
    from rsi_boot.memory.harvest import HARVEST_TITLES

    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    before = _yaml_count(store.rsi_dir)
    for title in HARVEST_TITLES:
        result = await svc.add(KnowledgeItem(
            project_id="p", title=title, content="自包含短知识", content_type="documentation",
        ))
        assert result["status"] == "error"
        assert result["code"] == "invalid"
        assert result["message"] == t("KNOWLEDGE_HARVEST_TITLE", locale_lang())
    assert _yaml_count(store.rsi_dir) == before


@pytest.mark.asyncio
async def test_add_rejects_distilled_without_run_id(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    before = _yaml_count(store.rsi_dir)
    result = await svc.add(KnowledgeItem(
        project_id="p", title="登录校验", content="验证码十分钟有效",
        content_type="documentation", tags=["signal:distilled"],
    ))
    assert result == {
        "status": "error",
        "code": "invalid",
        "message": t("KNOWLEDGE_DISTILL_TAGS", locale_lang()),
    }
    assert _yaml_count(store.rsi_dir) == before


@pytest.mark.asyncio
async def test_add_short_prohibition_succeeds(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    item_id = await svc.add(KnowledgeItem(
        project_id="p", title="禁止 SELECT *", content="必须列字段",
        content_type="prohibition",
    ))
    assert isinstance(item_id, str) and len(item_id) == 32
    got = store.read(item_id)
    assert got.status == "pending_review"
    assert got.content == "必须列字段"


def test_retype_distilled_for_pack_moves_skill_and_pattern(tmp_path):
    from rsi_boot.learning.retype_pack import retype_distilled_for_pack
    from rsi_boot.memory.store import memory_filename
    from rsi_boot.scanner.reading_packs import write_packs

    store = MemoryStore(tmp_path / ".rsi")
    write_packs(store.rsi_dir, bootstrap_run_id="run-1", packs=[
        {
            "id": "skills-0000",
            "domain": "skills",
            "title": "Skills",
            "sources": [{"kind": "skill", "path": ".cursor/skills/foo/SKILL.md", "name": "foo"}],
        },
        {
            "id": "patterns-0000",
            "domain": "patterns",
            "title": "Patterns",
            "sources": [{"kind": "docs", "path": ".cursor/knowledge/patterns/a.md"}],
        },
    ])
    skill = MemoryDoc(
        id="a" * 32,
        type="convention",
        title="Skill foo",
        content="Extend parser via FMPP; do not edit generated JavaCC.",
        tags=["pack_id:skills-0000", "signal:distilled", "bootstrap_run_id:run-1"],
    )
    store.write(skill, dest=official_dir(store.rsi_dir, "convention") / memory_filename(skill.title, skill.id))
    pattern = MemoryDoc(
        id="b" * 32,
        type="convention",
        title="Mask then unparse",
        content="Apply AST mask before dialect unparse.",
        tags=["pack_id:patterns-0000", "signal:distilled", "bootstrap_run_id:run-1"],
    )
    store.write(pattern, dest=official_dir(store.rsi_dir, "convention") / memory_filename(pattern.title, pattern.id))
    assert retype_distilled_for_pack(store, "skills-0000") == 1
    assert retype_distilled_for_pack(store, "patterns-0000") == 1
    got_skill = store.read("a" * 32)
    assert got_skill.type == "skill"
    assert got_skill.status == "active"
    assert (got_skill.payload or {}).get("name") == "foo"
    assert (got_skill.path or "").replace("\\", "/").endswith("skills/foo/skill.yaml")
    assert not list((tmp_path / ".rsi" / "memory" / "conventions").glob("skill-foo--aaaaaaaa.yaml"))
    got_pat = store.read("b" * 32)
    assert got_pat.type == "pattern"
    assert "patterns" in (got_pat.path or "").replace("\\", "/")
    names = {(d.payload or {}).get("name") for d in store.list_official("skill")}
    assert "foo" in names
    assert retype_distilled_for_pack(store, "skills-0000") == 0


@pytest.mark.asyncio
async def test_distilled_add_skips_injector_rewrite(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    calls: list[str] = []

    async def spy(project_id: str) -> None:
        calls.append(project_id)

    svc.on_change = spy
    distilled = await svc.add(KnowledgeItem(
        project_id="p", title="蒸馏条", content="自包含短知识正文",
        content_type="documentation",
        tags=["signal:distilled", "bootstrap_run_id:run-1"],
    ))
    assert isinstance(distilled, str)
    assert calls == []
    official = await svc.add(KnowledgeItem(
        project_id="p", title="禁止空密码", content="密码不能为空",
        content_type="prohibition", source_url="docs/a.md",
    ))
    assert isinstance(official, str)
    assert calls == ["p"]


@pytest.mark.asyncio
async def test_add_distilled_goes_pending_even_if_active(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    item_id = await svc.add(KnowledgeItem(
        project_id="p", title="登录校验", content="验证码十分钟有效",
        content_type="documentation", status="active",
        tags=["signal:distilled", "bootstrap_run_id:run-1"],
    ))
    assert isinstance(item_id, str)
    got = store.read(item_id)
    assert got.status == "pending_review"
    assert "pending" in (got.path or "").replace("\\", "/")


@pytest.mark.asyncio
async def test_knowledge_add_tool_passthrough_error(tmp_path):
    from rsi_boot.api.tools import knowledge_add_tool

    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    result = await knowledge_add_tool.handle(svc, {
        "title": "超长条",
        "content": "字" * 1501,
    })
    assert result["status"] == "error"
    assert result["code"] == "invalid"
    assert "success" not in result


@pytest.mark.asyncio
async def test_knowledge_add_infers_type_from_pack_domain(tmp_path):
    from rsi_boot.api.tools import knowledge_add_tool
    from rsi_boot.scanner.reading_packs import write_packs

    store = MemoryStore(tmp_path / ".rsi")
    write_packs(store.rsi_dir, bootstrap_run_id="run-1", packs=[{
        "id": "teaching-0000",
        "domain": "teaching",
        "title": "Teaching",
        "sources": [{"kind": "docs", "path": "t.md", "heading": "T"}],
    }])
    svc = KnowledgeService(store=store, project_root=tmp_path)
    result = await knowledge_add_tool.handle(svc, {
        "title": "Teaching: keep aliases",
        "content": "AST mask must keep user select aliases",
        "pack_id": "teaching-0000",
        "content_type": "convention",
    })
    assert result["status"] == "success"
    doc = store.read(result["id"])
    assert doc.type == "teaching_case"
    assert "teaching-cases" in (doc.path or "").replace("\\", "/")


@pytest.mark.asyncio
async def test_knowledge_review_writes_event(tmp_path):
    from rsi_boot.api.tools import knowledge_review_tool
    from rsi_boot.memory.logstore import iter_events

    store = MemoryStore(tmp_path / ".rsi")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    item_id = await svc.add(KnowledgeItem(
        project_id="p",
        title="登录校验",
        content="验证码十分钟有效",
        content_type="documentation",
        tags=["signal:distilled", "bootstrap_run_id:run-1"],
    ))

    rt = type("Rt", (), {"knowledge": svc, "decisions": None, "store": store})()
    out = await knowledge_review_tool.handle(rt, {
        "action": "approve",
        "id": item_id,
        "bootstrap_run_id": "run-1",
    })
    assert out["status"] == "success"
    events = [e for e in iter_events(store.rsi_dir, kinds={"review"})]
    assert events
    assert events[-1]["kind"] == "review"
    assert events[-1]["action"] == "approve"
    assert item_id in (events[-1].get("ids") or [])
