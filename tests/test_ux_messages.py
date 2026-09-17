from rsi_boot.ux.messages import STRINGS, TOOL_DESC, t


def _has_han(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def test_keys_have_zh_and_en():
    for key, pair in STRINGS.items():
        assert pair["zh"].strip() and pair["en"].strip(), key
        assert _has_han(pair["zh"]), key
        assert not _has_han(pair["en"]), key
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["zh"]
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["en"]


def test_t_en_has_no_han():
    assert not any("\u4e00" <= c <= "\u9fff" for c in t("MIGRATE_DONE", "en"))


def test_tool_desc_bilingual():
    assert "Call before" in TOOL_DESC["recall"]
    assert "任务开始前" in TOOL_DESC["recall"]
    assert "rsi_feedback" in TOOL_DESC["recall"]
    required = (
        "recall", "learn", "memory", "review", "feedback",
        "knowledge_add", "knowledge_search", "knowledge_delete",
        "harness", "stats", "conflicts",
    )
    for key in required:
        text = TOOL_DESC[key]
        assert any("\u4e00" <= c <= "\u9fff" for c in text), key
        assert any("A" <= c <= "Z" or "a" <= c <= "z" for c in text), key
    assert "rsi_query" not in TOOL_DESC


def test_list_tools_uses_module_tool_descriptions():
    import inspect

    from rsi_boot.api import mcp_server
    from rsi_boot.api.tools import (
        conflicts_tool,
        feedback_tool,
        knowledge_add_tool,
        knowledge_delete_tool,
        knowledge_review_tool,
        knowledge_search_tool,
        learn_tool,
        memory_tool,
        recall_tool,
        review_tool,
        stats_tool,
    )
    from rsi_boot.ux.messages import TOOL_DESC

    src = inspect.getsource(mcp_server.build_server)
    for name in (
        "recall_tool", "feedback_tool", "knowledge_add_tool", "knowledge_search_tool",
        "knowledge_delete_tool", "knowledge_review_tool", "review_tool", "conflicts_tool",
        "stats_tool", "learn_tool", "memory_tool",
    ):
        assert f"{name}.TOOL_DESCRIPTION" in src
    assert feedback_tool.TOOL_DESCRIPTION == TOOL_DESC["feedback"]
    assert knowledge_add_tool.TOOL_DESCRIPTION == TOOL_DESC["knowledge_add"]
    assert knowledge_search_tool.TOOL_DESCRIPTION == TOOL_DESC["knowledge_search"]
    assert knowledge_delete_tool.TOOL_DESCRIPTION == TOOL_DESC["knowledge_delete"]
    assert knowledge_review_tool.TOOL_DESCRIPTION == TOOL_DESC["review"]
    assert review_tool.TOOL_DESCRIPTION == TOOL_DESC["harness"]
    assert stats_tool.TOOL_DESCRIPTION == TOOL_DESC["stats"]
    assert conflicts_tool.TOOL_DESCRIPTION == TOOL_DESC["conflicts"]
    assert recall_tool.TOOL_DESCRIPTION == TOOL_DESC["recall"]
    assert learn_tool.TOOL_DESCRIPTION == TOOL_DESC["learn"]
    assert memory_tool.TOOL_DESCRIPTION == TOOL_DESC["memory"]
    assert "添加知识条目" not in src
    assert "记忆使用统计" not in src
    assert "rsi_query" not in src
