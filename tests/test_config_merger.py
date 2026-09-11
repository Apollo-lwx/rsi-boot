from rsi_boot.core.config_merger import merge_configs, merge_request_overrides


def test_deep_merge_two_layers():
    base = {"model": {"default": "a", "timeout_s": 60}, "budget": {"daily_token_cap": 0}}
    override = {"model": {"timeout_s": 30}}
    merged = merge_configs([base, override])
    assert merged["model"] == {"default": "a", "timeout_s": 30}
    assert merged["budget"]["daily_token_cap"] == 0


def test_list_is_replaced_not_merged():
    merged = merge_configs([{"tags": ["a", "b"]}, {"tags": ["c"]}])
    assert merged["tags"] == ["c"]


def test_protected_path_skipped_in_request_layer():
    merged = merge_request_overrides(
        {}, {"enhance": {"model": {"api_key": "sk-evil", "default": "mock"}}}
    )
    assert "api_key" not in merged["enhance"]["model"]
    assert merged["enhance"]["model"]["default"] == "mock"


def test_protected_path_allowed_in_file_layer():
    merged = merge_configs([{"enhance": {"model": {"api_key": "sk-x"}}}])
    assert merged["enhance"]["model"]["api_key"] == "sk-x"
