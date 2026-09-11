"""P2.2 LLM 意图分类测试（§3.1 Phase 2）：JSON 契约 / 门槛 / 降级链 / 缓存 / 角色"""

import asyncio
from types import SimpleNamespace

from rsi_boot.preprocessor.intent_classifier import _INTENT_CACHE, detect_intent_llm


class StubAdapter:
    def __init__(self, text: str = "", exc: Exception | None = None, delay: float = 0.0):
        self.text = text
        self.exc = exc
        self.delay = delay
        self.calls = 0

    async def complete(self, model, messages):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text)


def setup_function():
    _INTENT_CACHE.clear()


async def test_llm_classifies_with_json_contract():
    adapter = StubAdapter('{"intent": "debug", "confidence": 0.92}')
    intent, conf = await detect_intent_llm("为什么这个测试一直失败", adapter=adapter)
    assert (intent, conf) == ("debug", 0.92)
    assert adapter.calls == 1


async def test_llm_low_confidence_falls_back():
    adapter = StubAdapter('{"intent": "debug", "confidence": 0.4}')
    intent, conf = await detect_intent_llm("随便聊聊", adapter=adapter)
    assert (intent, conf) == ("general_assist", 0.5)


async def test_llm_invalid_json_degrades_to_rules():
    adapter = StubAdapter("I cannot classify this")
    # 规则版能命中 debug 关键词
    intent, conf = await detect_intent_llm("帮我 debug 这个报错", adapter=adapter)
    assert intent == "debug"


async def test_llm_timeout_degrades_to_rules():
    adapter = StubAdapter('{"intent": "code_gen", "confidence": 0.9}', delay=10)
    intent, _ = await detect_intent_llm("帮我实现一个功能", adapter=adapter)
    assert intent == "code_gen"  # 规则版命中「实现」


async def test_llm_exception_degrades_to_rules():
    adapter = StubAdapter(exc=RuntimeError("boom"))
    intent, _ = await detect_intent_llm("explain this code", adapter=adapter)
    assert intent == "explain"


async def test_llm_unknown_intent_degrades_to_rules():
    adapter = StubAdapter('{"intent": "not_a_real_intent", "confidence": 0.9}')
    intent, _ = await detect_intent_llm("review 这段代码", adapter=adapter)
    assert intent == "code_review"


async def test_result_cached():
    adapter = StubAdapter('{"intent": "explain", "confidence": 0.88}')
    await detect_intent_llm("什么是依赖注入", adapter=adapter)
    intent, conf = await detect_intent_llm("什么是依赖注入", adapter=adapter)
    assert (intent, conf) == ("explain", 0.88)
    assert adapter.calls == 1  # 第二次命中缓存


async def test_role_catalog_used():
    adapter = StubAdapter('{"intent": "test_gen", "confidence": 0.9}')
    intent, _ = await detect_intent_llm("为这个接口生成测试用例", role="test", adapter=adapter)
    assert intent == "test_gen"


async def test_role_unknown_intent_rejected():
    """角色请求返回非该角色意图集的 intent → 降级规则版"""
    adapter = StubAdapter('{"intent": "code_gen", "confidence": 0.9}')
    intent, _ = await detect_intent_llm("随便输入一些没有规则命中的内容 xyz", role="test", adapter=adapter)
    assert intent == "general_assist"  # 规则版兜底


async def test_no_adapter_uses_rules():
    intent, _ = await detect_intent_llm("review my code")
    assert intent == "code_review"
