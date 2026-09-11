from rsi_boot.preprocessor.intent_classifier import detect_intent


def test_code_review_intent():
    intent, confidence = detect_intent("帮我审查这段代码")
    assert intent == "code_review"
    assert 0 < confidence <= 1


def test_debug_intent():
    intent, _ = detect_intent("这个报错怎么解决 Traceback")
    assert intent == "debug"


def test_fallback_intent():
    intent, confidence = detect_intent("今天天气怎么样")
    assert intent == "general_assist"
    assert confidence <= 0.5
