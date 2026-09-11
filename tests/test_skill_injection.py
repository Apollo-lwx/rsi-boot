"""P3.6 技能槽管线注入冒烟（§3.9）：trigger 命中 → 指令随提示词注入，标注 skill:<name>"""

import pytest

from rsi_boot.core.models import RSIRequest
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.learning.skill_loader import SkillLoader
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.orchestrator.pipeline import Pipeline


def _config():
    return {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000},
        "embedding": {"provider": "mock"},
    }


async def test_skill_matched_and_injected(db, tmp_path):
    skill_dir = tmp_path / "skills" / "tdd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: 测试驱动开发\ntrigger:\n  keywords: [单元测试]\n---\n先写失败测试再实现。\n",
        encoding="utf-8",
    )
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    pipeline = Pipeline(db, config, retriever, adapter, skill_loader=SkillLoader([tmp_path / "skills"]))

    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="如何为这个函数写单元测试？"))
    assert resp.status == "success"
    # mock 回显系统提示词长度：技能注入后 system 变长；直接验证注入方法的产出
    injected = pipeline._inject_skills("general_assist", "如何为这个函数写单元测试？", [])
    assert len(injected) == 1
    assert injected[0].title == "skill:tdd"
    assert "先写失败测试" in injected[0].content


async def test_skill_miss_returns_original(db, tmp_path):
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    pipeline = Pipeline(db, config, retriever, adapter, skill_loader=SkillLoader([tmp_path]))

    assert pipeline._inject_skills("explain", "完全无关的输入", []) == []
