from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService


async def _add(service: KnowledgeService, title: str, content: str, project: str = "p1", roles=None):
    item = KnowledgeItem(project_id=project, title=title, content=content, roles=roles or [])
    return await service.add(item)


async def test_fts_english_query(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add(service, "Deploy guide", "Use docker compose to deploy the service stack")
    await _add(service, "Unrelated", "Banana bread recipe with walnuts")

    results = await retriever.search("docker deploy", "p1")
    assert len(results) == 1
    assert results[0].title == "Deploy guide"


async def test_cjk_query_matches(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add(service, "项目命名约定", "本项目所有 Python 标识符统一使用 snake_case")

    results = await retriever.search("本项目的命名约定是什么", "p1")
    assert len(results) == 1
    assert "snake_case" in results[0].content


async def test_project_isolation(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add(service, "Deploy guide", "docker compose deploy", project="p1")

    assert await retriever.search("docker", "other-project") == []


async def test_role_filter(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add(service, "Dev only", "docker internal registry", roles=["developer"])

    assert await retriever.search("docker", "p1", role="pm") == []
    assert len(await retriever.search("docker", "p1", role="developer")) == 1


async def test_fts_injection_safe(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add(service, "Doc", "some content here")
    # FTS5 操作符注入不应抛错
    results = await retriever.search('" OR 1=1 NEAR/*', "p1")
    assert isinstance(results, list)
