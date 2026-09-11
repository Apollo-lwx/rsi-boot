import pytest

from rsi_boot.config.loader import load_config
from rsi_boot.core.config_merger import validate_config
from rsi_boot.core.exceptions import ConfigError


def test_plaintext_key_in_project_config_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    (tmp_path / "rsi-boot.yaml").write_text(
        "enhance:\n  model:\n    api_key: sk-abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="明文密钥"):
        load_config(project_root=tmp_path)


def test_env_ref_resolved_in_user_config(tmp_path, monkeypatch):
    rsi_home = tmp_path / ".rsi"
    rsi_home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(rsi_home))
    monkeypatch.setenv("MY_LLM_KEY", "sk-resolved-key-value")
    (rsi_home / "config.yaml").write_text(
        "enhance:\n  model:\n    api_key: env:MY_LLM_KEY\n", encoding="utf-8"
    )
    config = load_config()
    assert config["enhance"]["model"]["api_key"] == "sk-resolved-key-value"


def test_env_ref_missing_var_rejected(tmp_path, monkeypatch):
    rsi_home = tmp_path / ".rsi"
    rsi_home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(rsi_home))
    monkeypatch.delenv("NO_SUCH_VAR", raising=False)
    (rsi_home / "config.yaml").write_text(
        "enhance:\n  model:\n    api_key: ${NO_SUCH_VAR}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="NO_SUCH_VAR"):
        load_config()


def test_legacy_model_key_warns(tmp_path, monkeypatch, caplog):
    """v3.0：v2.6 的 model.* 命名空间检出告警（主链路忽略，不拒绝启动）"""
    rsi_home = tmp_path / ".rsi"
    rsi_home.mkdir()
    monkeypatch.setenv("RSI_HOME", str(rsi_home))
    (rsi_home / "config.yaml").write_text("model:\n  default: gpt-4o-mini\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        config = load_config()
    assert "v2.6" in caplog.text and "enhance.*" in caplog.text
    assert "model" in config  # 旧键保留在合并结果中（主链路不读取）


def test_validate_config_type_error():
    with pytest.raises(ConfigError, match="retrieval.top_n"):
        validate_config({"retrieval": {"top_n": "five"}})


def test_validate_config_range_error():
    with pytest.raises(ConfigError, match="超出范围"):
        validate_config({"enhance": {"model": {"timeout_s": 99999}}})


def test_validate_config_aggregates_violations():
    with pytest.raises(ConfigError) as exc_info:
        validate_config({"enhance": {"model": {"timeout_s": "x"}}, "retrieval": {"top_n": 0}})
    assert "timeout_s" in str(exc_info.value)
    assert "top_n" in str(exc_info.value)


def test_validate_config_bool_rejected():
    with pytest.raises(ConfigError):
        validate_config({"retrieval": {"top_n": True}})


def test_validate_config_ok():
    validate_config({"enhance": {"model": {"default": "mock", "timeout_s": 60}},
                     "retrieval": {"top_n": 5}})
