from rsi_boot.core.models import KnowledgeItem, RSIRequest
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.orchestrator.pipeline import Pipeline
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.log_service import LogService


def _pipeline(db, config):
    retriever = KnowledgeRetriever(db, config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    return Pipeline(db, config, retriever, adapter)


async def test_end_to_end_with_mock(db, base_config):
    pipeline = _pipeline(db, base_config)
    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="帮我写个快排"))
    assert resp.status == "success"
    assert resp.content[0].body
    assert resp.feedback_token
    assert resp.cost_info.model == "mock"

    # 两段式日志：终态已落库
    logs = LogService(db)
    ctx = await logs.get_token_context(resp.feedback_token)
    assert ctx is not None and ctx[1] == "u1"


async def test_knowledge_injection_in_prompt(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await service.add(
        KnowledgeItem(project_id="default", title="约定", content="统一使用 snake_case 命名")
    )
    pipeline = _pipeline(db, base_config)
    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="本项目的命名约定"))
    assert "含注入知识" in resp.content[0].body


async def test_budget_exceeded_returns_partial(db, base_config):
    base_config["budget"]["daily_token_cap"] = 1  # 首次调用后即超限
    pipeline = _pipeline(db, base_config)
    await pipeline.run(RSIRequest(user_id="u1", raw_input="第一次"))
    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="第二次"))
    assert resp.status == "partial"

    # force=true 覆盖
    resp_forced = await pipeline.run(
        RSIRequest(user_id="u1", raw_input="强制", preferences={"force": True})
    )
    assert resp_forced.status == "success"


async def test_model_error_returns_error_status(db, base_config):
    base_config["model"]["default"] = "gpt-4o-mini"  # 无 API key → OpenAI provider 失败
    base_config["model"]["max_retries"] = 0
    pipeline = _pipeline(db, base_config)
    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="hello"))
    assert resp.status == "error"
    assert resp.error is not None
