import pytest

from rsi_boot.core import masking
from rsi_boot.core.masking import MASKING_RULES, mask_messages, mask_text


@pytest.fixture(autouse=True)
def reset_rules():
    yield
    masking.configure({})  # 每个测试后恢复默认规则集，避免全局状态泄漏


def test_rule_count_matches_spec():
    assert len(MASKING_RULES) == 10


def test_email():
    assert mask_text("联系 admin@example.com 即可") == "联系 ***@example.com 即可"


def test_ipv4():
    # 公网 IP 保留前两段；内网 IP 由 internal_ip 规则处理（见 test_internal_ip）
    assert mask_text("服务器 203.0.113.9 宕机") == "服务器 203.0.*.* 宕机"


def test_openai_key():
    assert mask_text("key 是 sk-abcdefghijklmnopqrstuvwxyz") == "key 是 sk-****"


def test_aws_akid():
    assert mask_text("AKIAIOSFODNN7EXAMPLE") == "AKIA****"


def test_github_token():
    assert mask_text("ghp_" + "a" * 36) == "gh*_****"


def test_slack_token():
    assert mask_text("xoxb-1234567890-abcdef") == "xox*-****"


def test_jwt():
    jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    assert mask_text(jwt) == "eyJ****"


def test_private_key():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert mask_text(pem) == "[PRIVATE KEY REDACTED]"


def test_password_field():
    assert mask_text("password: hunter2") == "password=****"
    # 闭合引号按 §8.1 规则表原样保留（值已脱敏）
    assert mask_text('SECRET = "s3cr3t9"') == 'SECRET=****"'


def test_internal_ip():
    assert mask_text("内网 192.168.1.10 可通") == "内网 [INTERNAL_IP] 可通"
    assert mask_text("172.16.0.1") == "[INTERNAL_IP]"
    assert mask_text("10.0.0.8") == "[INTERNAL_IP]"


def test_public_ip_not_internal_masked_as_ipv4():
    # 公网 IP 走 ipv4 规则（保留前两段）
    assert mask_text("8.8.8.8") == "8.8.*.*"


def test_mask_messages_dual_scan():
    messages = [
        {"role": "system", "content": "参考知识：key sk-abcdefghijklmnopqrstuvwxyz"},
        {"role": "user", "content": "我的邮箱 bob@corp.com"},
    ]
    masked = mask_messages(messages)
    assert "sk-****" in masked[0]["content"]
    assert "***@corp.com" in masked[1]["content"]


def test_disabled_rules():
    masking.configure({"masking": {"disabled_rules": ["email"]}})
    assert "admin@example.com" in mask_text("admin@example.com")


def test_extra_rules():
    masking.configure({"masking": {"extra_rules": [
        {"name": "custom_token", "pattern": r"CT-[0-9]{6}", "replacement": "CT-****"},
    ]}})
    assert mask_text("token CT-123456") == "token CT-****"
