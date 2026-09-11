"""P4.1/P4.2：多模型适配（Anthropic/DeepSeek）、熔断器、fallback 链、成本路由。"""

import sys
import types

import pytest

from rsi_boot.common.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, State
from rsi_boot.core.exceptions import CircuitOpenError, ModelProviderError, ModelTimeoutError
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.base import ModelResult
from rsi_boot.model.providers.mock import MockProvider
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.strategy.cost_router import CostRouter
from rsi_boot.strategy.engine import StrategyEngine


# ---------- 测试替身 ----------


class _StubRegistry:
    """按模型名返回预置 Provider（duck-type 替代 ModelRegistry）"""

    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, model_name):
        return self._providers[model_name]


class _FailProvider:
    name = "fail"

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def complete(self, model, messages, timeout_s):
        self.calls += 1
        raise self._exc


def _adapter(config, providers):
    return ModelAdapter(_StubRegistry(providers), config)


def _ok_result(model):
    return ModelResult(text="ok", model=model, prompt_tokens=10, completion_tokens=5, latency_ms=1)


# ---------- P4.1：Registry 前缀路由 ----------


def test_registry_mock_prefix(base_config):
    registry = ModelRegistry(base_config)
    assert isinstance(registry.get_provider("mock"), MockProvider)
    assert registry.get_provider("mock-8k") is registry.get_provider("mock")  # 同 key 复用


def test_registry_deepseek_requires_key(base_config, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    registry = ModelRegistry(base_config)
    with pytest.raises(ModelProviderError, match="DeepSeek API Key"):
        registry.get_provider("deepseek-chat")


def test_registry_deepseek_base_url(base_config, monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    registry = ModelRegistry(base_config)
    provider = registry.get_provider("deepseek-reasoner")
    assert provider.name == "deepseek"
    assert str(provider._client.base_url).startswith("https://api.deepseek.com")


def test_registry_anthropic_requires_key(base_config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    registry = ModelRegistry(base_config)
    with pytest.raises(ModelProviderError, match="Anthropic API Key"):
        registry.get_provider("claude-3-5-sonnet")


def test_registry_openai_default(base_config, monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    cfg = {**base_config, "model": {**base_config["model"], "api_key": "test-key"}}
    registry = ModelRegistry(cfg)
    assert registry.get_provider("gpt-4o-mini").name == "openai"


# ---------- P4.1：Anthropic 协议映射（伪 SDK 注入） ----------


class _FakeAnthropicUsage:
    input_tokens = 11
    output_tokens = 7


class _FakeAnthropicBlock:
    type = "text"
    text = "你好"


class _FakeAnthropicResp:
    content = [_FakeAnthropicBlock()]
    usage = _FakeAnthropicUsage()


class _FakeAsyncAnthropic:
    last_create_kwargs = None
    raise_exc = None

    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key
        self.base_url = base_url
        self.messages = self

    async def create(self, **kwargs):
        type(self).last_create_kwargs = kwargs
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return _FakeAnthropicResp()


@pytest.fixture
def fake_anthropic(monkeypatch):
    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = _FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    _FakeAsyncAnthropic.last_create_kwargs = None
    _FakeAsyncAnthropic.raise_exc = None
    return module


async def test_anthropic_system_extracted(fake_anthropic):
    from rsi_boot.model.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key="k")
    result = await provider.complete(
        "claude-3-5-haiku",
        [
            {"role": "system", "content": "你是助手"},
            {"role": "system", "content": "第二段"},
            {"role": "user", "content": "hi"},
        ],
        timeout_s=5,
    )
    kwargs = _FakeAsyncAnthropic.last_create_kwargs
    assert kwargs["system"] == "你是助手\n第二段"  # system 合并为独立参数
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]  # system 不进 messages
    assert kwargs["max_tokens"] == 4096  # Anthropic 必填
    assert result.text == "你好"
    assert result.prompt_tokens == 11  # usage 映射 input/output_tokens
    assert result.completion_tokens == 7


async def test_anthropic_4xx_not_countable(fake_anthropic):
    from rsi_boot.model.providers.anthropic import AnthropicProvider

    class _BadRequest(Exception):
        status_code = 400

    _FakeAsyncAnthropic.raise_exc = _BadRequest("bad request")
    provider = AnthropicProvider(api_key="k")
    with pytest.raises(ModelProviderError) as exc_info:
        await provider.complete("claude-3-5-haiku", [{"role": "user", "content": "hi"}], 5)
    assert exc_info.value.countable is False  # §5.1：4xx 不计入熔断


async def test_anthropic_429_countable(fake_anthropic):
    from rsi_boot.model.providers.anthropic import AnthropicProvider

    class _RateLimit(Exception):
        status_code = 429

    _FakeAsyncAnthropic.raise_exc = _RateLimit("rate limited")
    provider = AnthropicProvider(api_key="k")
    with pytest.raises(ModelProviderError) as exc_info:
        await provider.complete("claude-3-5-haiku", [{"role": "user", "content": "hi"}], 5)
    assert exc_info.value.countable is True  # 429 属服务端限流，计入熔断


# ---------- P4.2：熔断器状态机 ----------


def test_breaker_trips_after_threshold():
    breaker = CircuitBreaker("t", threshold=3, recovery_timeout_s=60)
    for _ in range(2):
        breaker.on_failure()
    assert breaker.state is State.CLOSED  # 未达阈值
    breaker.on_failure()
    assert breaker.state is State.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.allow_request()  # OPEN fail-fast


def test_breaker_success_resets_count():
    breaker = CircuitBreaker("t", threshold=2, recovery_timeout_s=60)
    breaker.on_failure()
    breaker.on_success()  # 任何一次成功立即清零
    breaker.on_failure()
    assert breaker.state is State.CLOSED


def test_breaker_non_countable_failure_ignored():
    breaker = CircuitBreaker("t", threshold=1, recovery_timeout_s=60)
    breaker.on_failure(countable=False)  # HTTP 4xx 口径
    assert breaker.state is State.CLOSED
    assert breaker.failure_count == 0


def test_breaker_half_open_probe_success():
    breaker = CircuitBreaker("t", threshold=1, recovery_timeout_s=0.01)
    breaker.on_failure()
    assert breaker.state is State.OPEN
    import time

    time.sleep(0.02)
    breaker.allow_request()  # 超过恢复时间 → HALF_OPEN 放行探测
    assert breaker.state is State.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        breaker.allow_request()  # 探测中并发请求 fail-fast
    breaker.on_success()
    assert breaker.state is State.CLOSED


def test_breaker_half_open_probe_failure_reopens():
    breaker = CircuitBreaker("t", threshold=1, recovery_timeout_s=0.01)
    breaker.on_failure()
    import time

    time.sleep(0.02)
    breaker.allow_request()
    breaker.on_failure()  # 探测失败 → 回 OPEN 重新计时
    assert breaker.state is State.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.allow_request()


def test_breaker_registry_states():
    registry = CircuitBreakerRegistry()
    registry.get("a", threshold=1).on_failure()
    registry.get("b")
    assert registry.states() == {"a": "open", "b": "closed"}


# ---------- P4.2：Adapter fallback 链 ----------


async def test_fallback_on_primary_failure(base_config):
    cfg = {**base_config, "model": {**base_config["model"], "fallback": ["mock"]}}
    failing = _FailProvider(ModelProviderError("boom"))
    adapter = _adapter(cfg, {"gpt-4o-mini": failing, "mock": MockProvider()})

    result = await adapter.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
    assert result.fallback_used is True
    assert result.model == "mock"
    assert failing.calls == 1  # max_retries=0


async def test_fallback_skipped_when_primary_ok(base_config):
    cfg = {**base_config, "model": {**base_config["model"], "fallback": ["mock"]}}
    adapter = _adapter(cfg, {"mock": MockProvider()})
    result = await adapter.complete("mock", [{"role": "user", "content": "hi"}])
    assert result.fallback_used is False


async def test_all_chain_failed_raises(base_config):
    cfg = {**base_config, "model": {**base_config["model"], "fallback": ["mock-b"]}}
    adapter = _adapter(cfg, {
        "mock-a": _FailProvider(ModelProviderError("x")),
        "mock-b": _FailProvider(ModelTimeoutError("y")),
    })
    with pytest.raises(ModelProviderError, match="全部失败"):
        await adapter.complete("mock-a", [{"role": "user", "content": "hi"}])


async def test_open_breaker_skips_primary(base_config):
    cfg = {**base_config, "model": {**base_config["model"], "fallback": ["mock"]}}
    failing = _FailProvider(ModelProviderError("boom"))
    adapter = _adapter(cfg, {"gpt-4o-mini": failing, "mock": MockProvider()})

    for _ in range(5):  # 连续失败 5 次 → 熔断 OPEN
        await adapter.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
    assert failing.calls == 5
    assert adapter.breaker_states()["llm:gpt-4o-mini"] == "open"

    await adapter.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
    assert failing.calls == 5  # OPEN 后 fail-fast，不再调用主模型


async def test_4xx_failure_does_not_trip_breaker(base_config):
    cfg = {**base_config, "model": {**base_config["model"], "fallback": ["mock"]}}
    failing = _FailProvider(ModelProviderError("bad request", countable=False))
    adapter = _adapter(cfg, {"gpt-4o-mini": failing, "mock": MockProvider()})

    for _ in range(6):  # 超过阈值但全部 countable=False
        await adapter.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
    assert failing.calls == 6  # 熔断未触发，每次都真实调用
    assert adapter.breaker_states()["llm:gpt-4o-mini"] == "closed"


# ---------- P4.2：成本路由 ----------


def _cr_config(**cr):
    return {"model": {"default": "gpt-4o"}, "cost_routing": cr}


def test_cost_router_disabled_returns_default():
    router = CostRouter(_cr_config(enabled=False))
    assert router.enabled is False
    assert router.route("general_assist") == "gpt-4o"


def test_cost_router_low_tier_cheapest():
    router = CostRouter(_cr_config(enabled=True))
    # low 层级上限 1.0：deepseek-chat(1.10) 排除，gpt-4o-mini(0.60) 入选且最便宜
    assert router.route("general_assist") == "gpt-4o-mini"


def test_cost_router_high_tier_cheapest_overall():
    router = CostRouter(_cr_config(enabled=True))
    assert router.route("code_gen") == "gpt-4o-mini"  # high 不设限，全局最便宜


def test_cost_router_explicit_candidates():
    router = CostRouter(_cr_config(enabled=True))
    chosen = router.route("code_gen", candidates=["gpt-4o", "claude-3-5-haiku"])
    assert chosen == "claude-3-5-haiku"  # 4.00 < 10.00


def test_cost_router_no_candidate_falls_back_to_default():
    router = CostRouter(_cr_config(enabled=True, tiers={"low": {"max_completion_price": 0.5}}))
    assert router.route("general_assist") == "gpt-4o"  # 层级内无人达标 → 默认模型


def test_cost_router_intent_tier_override():
    router = CostRouter(_cr_config(enabled=True, intent_tier={"code_gen": "low"}))
    assert router.route("code_gen") == "gpt-4o-mini"


def test_cost_router_unknown_intent_defaults_high():
    router = CostRouter(_cr_config(enabled=True))
    assert router.route("never_seen_intent") == "gpt-4o-mini"  # 未映射意图按 high 不设限


# ---------- P4.2：策略引擎兜底路由接线 ----------


async def test_engine_fallback_uses_cost_router(db, base_config):
    cfg = {**base_config, "model": {**base_config["model"], "default": "gpt-4o"},
           "cost_routing": {"enabled": True}}
    engine = StrategyEngine(db, cfg)
    decision = await engine.decide("p1", "general_assist")
    assert decision.strategy_name == "default"
    assert decision.model_name == "gpt-4o-mini"  # 成本路由改写了兜底模型


async def test_engine_fallback_cost_router_disabled(db, base_config):
    cfg = {**base_config, "model": {**base_config["model"], "default": "gpt-4o"}}
    engine = StrategyEngine(db, cfg)
    decision = await engine.decide("p1", "general_assist")
    assert decision.model_name == "gpt-4o"  # 默认关闭，行为不变
