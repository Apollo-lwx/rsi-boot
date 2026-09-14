import json
import uuid
from datetime import datetime, timezone

from rsi_boot.knowledge.retriever import KnowledgeRetriever


async def _seed(db, title: str, content: str, project: str = "p1", roles=None):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, roles, tags, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'documentation', ?, '[]', 'active', ?, ?)",
        (item_id, project, title, content, json.dumps(roles or []), now, now),
    )
    await conn.commit()
    return item_id


async def test_fts_english_query(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    await _seed(db, "Deploy guide", "Use docker compose to deploy the service stack")
    await _seed(db, "Unrelated", "Banana bread recipe with walnuts")

    results = await retriever.search("docker deploy", "p1")
    assert len(results) == 1
    assert results[0].title == "Deploy guide"


async def test_cjk_query_matches(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    await _seed(db, "项目命名约定", "本项目所有 Python 标识符统一使用 snake_case")

    results = await retriever.search("本项目的命名约定是什么", "p1")
    assert len(results) == 1
    assert "snake_case" in results[0].content


async def test_project_isolation(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    await _seed(db, "Deploy guide", "docker compose deploy", project="p1")

    assert await retriever.search("docker", "other-project") == []


async def test_role_filter(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    await _seed(db, "Dev only", "docker internal registry", roles=["developer"])

    assert await retriever.search("docker", "p1", role="pm") == []
    assert len(await retriever.search("docker", "p1", role="developer")) == 1


async def test_fts_injection_safe(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    await _seed(db, "Doc", "some content here")
    # FTS5 操作符注入不应抛错
    results = await retriever.search('" OR 1=1 NEAR/*', "p1")
    assert isinstance(results, list)
