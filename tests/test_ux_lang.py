from rsi_boot.ux.lang import detect_lang


def test_detect_zh_from_han():
    assert detect_lang("修一下查询") == "zh"
    assert detect_lang("Fix the 禁止项 now") == "zh"
    assert detect_lang("修一下 SELECT * FROM t") == "zh"


def test_detect_en_from_letters():
    assert detect_lang("Fix the SQL query") == "en"


def test_preserve_sql_not_translated():
    from rsi_boot.ux.messages import t
    msg = t("TEACH_RECORDED", "zh", path="memory/x.yaml")
    assert "memory/x.yaml" in msg


def test_explicit_wins():
    assert detect_lang("修一下", explicit="en") == "en"
