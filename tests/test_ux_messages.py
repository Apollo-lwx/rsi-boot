from rsi_boot.ux.messages import STRINGS, TOOL_DESC, t


def test_keys_have_zh_and_en():
    for key, pair in STRINGS.items():
        assert pair["zh"].strip() and pair["en"].strip(), key
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["zh"]
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["en"]


def test_t_en_has_no_han():
    assert not any("\u4e00" <= c <= "\u9fff" for c in t("MIGRATE_DONE", "en"))


def test_tool_desc_bilingual():
    assert "Call before" in TOOL_DESC["recall"]
    assert "任务开始前" in TOOL_DESC["recall"]
    assert "rsi_feedback" in TOOL_DESC["recall"]
