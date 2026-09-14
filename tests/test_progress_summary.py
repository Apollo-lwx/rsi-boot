from io import StringIO
from rsi_boot.cli.progress import Progress
from rsi_boot.ux.messages import t


def test_finish_custom_summary():
    buf = StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(1, title="t")
    p.phase(t("MIGRATE_PHASE_KNOWLEDGE", "zh"), total=1)
    p.tick(1)
    p.finish(t("MIGRATE_HALF", "zh")[:8], summary=t("MIGRATE_DONE", "zh"))
    text = buf.getvalue()
    assert "迁出完成" in text
    assert "学习完成" not in text


def test_mid_phase_line_follows_lang(monkeypatch):
    buf = StringIO()
    monkeypatch.setenv("RSI_LANG", "en")
    p = Progress(stream=buf, min_interval=0)
    p.start(1)
    p.phase("Open", total=10)
    p.tick(3)
    text = buf.getvalue()
    assert "elapsed" in text.lower() or "eta" in text.lower()
    assert "已用" not in text
    assert "约剩" not in text


def test_finish_en_migrate_not_chinese_done():
    buf = StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(1)
    p.phase(t("MIGRATE_PHASE_KNOWLEDGE", "en"), total=1)
    p.tick(1)
    p.finish(summary=t("MIGRATE_DONE", "en"))
    assert "Migration complete" in buf.getvalue()
    assert "学习完成" not in buf.getvalue()
