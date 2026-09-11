"""角色基础设施（§3.6）：角色意图注册 + 角色模板解析。"""

from rsi_boot.core.models import ProcessedRequest, RSIRequest
from rsi_boot.preprocessor.intent_classifier import detect_intent, registered_roles
from rsi_boot.strategy.prompt_renderer import PromptRenderer


def test_role_intent_takes_precedence():
    # "写测试" 命中 test 角色的 test_gen；无 role 时走通用兜底
    intent, _ = detect_intent("帮我写测试覆盖这个函数", role="test")
    assert intent == "test_gen"
    intent_general, _ = detect_intent("帮我写测试覆盖这个函数")
    assert intent_general != "test_gen"


def test_pm_role_intent():
    intent, _ = detect_intent("帮我写 PRD 文档初稿", role="pm")
    assert intent == "prd_gen"


def test_role_falls_back_to_general():
    # 角色规则未命中时走通用意图
    intent, _ = detect_intent("帮我审查这段代码", role="test")
    assert intent == "code_review"


def test_unknown_role_uses_general():
    intent, _ = detect_intent("写测试", role="no-such-role")
    assert intent in {"code_gen", "general_assist"}


def test_registered_roles():
    assert registered_roles() == ["pm", "test"]


def test_role_template_resolves():
    renderer = PromptRenderer()
    request = RSIRequest(user_id="u1", raw_input="写测试", role="test")
    processed = ProcessedRequest(request=request, intent="test_gen", confidence=0.85)
    messages = renderer.render("test_gen.jinja2", processed)
    assert "测试用例生成" in messages[0]["content"]


def test_missing_template_falls_back():
    renderer = PromptRenderer()
    request = RSIRequest(user_id="u1", raw_input="x")
    processed = ProcessedRequest(request=request, intent="unknown_intent", confidence=0.5)
    messages = renderer.render("no_such_template.jinja2", processed)
    assert "通用协助" in messages[0]["content"]
