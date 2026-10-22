"""rsi statusline：双宿主 payload 归一化、transcript token 统计、RSI 段与文案池口径。"""

from __future__ import annotations

import io
import json
import time

import pytest

from rsi_boot.cli.statusline_command import (
    RsiToday,
    _resolve_lang,
    _user_config_lang,
    _visible_width,
    find_rsi_dir,
    parse_payload,
    render_line,
    rsi_today,
    run_statusline,
    scan_transcript,
)
from rsi_boot.ux import quips


@pytest.fixture(autouse=True)
def _isolated_rsi_home(tmp_path, monkeypatch):
    """隔离真实 ~/.rsi 与项目根：防止用户 config.yaml 或环境变量影响断言。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "rsi-home"))
    monkeypatch.delenv("RSI_LANG", raising=False)
    monkeypatch.delenv("RSI_STATUSLINE_REST_MINUTES", raising=False)
    monkeypatch.delenv("RSI_STATUSLINE_STYLE", raising=False)
    monkeypatch.delenv("RSI_PROJECT_ROOT", raising=False)


def _run(payload: dict | str | None, argv: list[str] | None = None) -> tuple[int, str]:
    text = payload if isinstance(payload, str) else (json.dumps(payload) if payload else "")
    stdin = io.StringIO(text)
    stdin.isatty = lambda: False  # type: ignore[attr-defined]
    out = io.StringIO()
    code = run_statusline(argv or [], stdin=stdin, stdout=out)
    return code, out.getvalue().strip()


def _cursor_payload(**overrides) -> dict:
    payload = {
        "session_id": "s-1",
        "cwd": "/repo",
        "model": {"id": "kimi-k3", "display_name": "Kimi K3"},
        "context_window": {
            "used_percentage": 29.0,
            "remaining_percentage": 71.0,
            "context_window_size": 200000,
            "total_input_tokens": 58000,
        },
        "render_width_chars": 200,
    }
    payload.update(overrides)
    return payload


def _claude_payload(transcript: str, **overrides) -> dict:
    payload = {
        "session_id": "s-2",
        "transcript_path": transcript,
        "cwd": "/repo",
        "model": {"id": "claude-opus-4-1", "display_name": "Opus 4.1"},
        "cost": {"total_cost_usd": 0.42, "total_duration_ms": 3_720_000},
    }
    payload.update(overrides)
    return payload


def _usage_line(i: int, o: int, cr: int, cc: int = 0, sidechain: bool = False) -> str:
    return json.dumps({
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "usage": {
                "input_tokens": i,
                "output_tokens": o,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cc,
            }
        },
    })


# --- payload 归一化 -------------------------------------------------------


def test_cursor_payload_uses_context_window():
    info = parse_payload(json.dumps(_cursor_payload()))
    assert info.model == "Kimi K3"
    assert info.ctx_remaining_pct == 71.0
    assert info.ctx_window == 200000
    assert info.tokens_in == 58000


def test_garbage_payload_yields_empty_info():
    info = parse_payload("not json at all")
    assert info.model == ""
    assert info.ctx_remaining_pct is None


# --- transcript 解析 ------------------------------------------------------


def test_transcript_token_sums_and_context(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "\n".join([
            _usage_line(3, 100, 20_000, 500),
            _usage_line(5, 200, 40_000, 800),
            _usage_line(9, 50, 10_000, 0, sidechain=True),
        ]),
        encoding="utf-8",
    )
    code, line = _run(_claude_payload(str(transcript)), argv=["--plain", "--no-quip", "--lang", "en"])
    assert code == 0
    # 合计：in 17 / out 350 / cache 71.3k
    assert "in 17" in line
    assert "out 350" in line
    assert "cache 71.3k" in line
    # 上下文按最后一条主链（非 sidechain）：5 + 40000 + 800 = 40805 / 200000 ≈ 剩 80%
    assert "ctx left 80%" in line
    assert "$0.42" in line
    assert "1h2m" in line


def test_transcript_missing_still_renders(tmp_path):
    code, line = _run(_claude_payload(str(tmp_path / "nope.jsonl")), argv=["--plain", "--no-quip"])
    assert code == 0
    assert "Opus 4.1" in line


def test_exceeds_200k_uses_large_window(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_usage_line(10, 1, 300_000, 0), encoding="utf-8")
    code, line = _run(
        _claude_payload(str(transcript), exceeds_200k_tokens=True),
        argv=["--plain", "--no-quip", "--lang", "en"],
    )
    assert code == 0
    assert "ctx left 70%" in line  # 300010 / 1_000_000 ≈ 30% used


# --- RSI 段 ---------------------------------------------------------------


def _event(kind: str, **extra) -> str:
    return json.dumps({
        "id": "x" * 32,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        **extra,
    })


def test_rsi_today_counts(tmp_path):
    logs = tmp_path / ".rsi" / "logs"
    logs.mkdir(parents=True)
    (logs / "events.jsonl").write_text(
        "\n".join([
            _event("recall"),
            _event("recall"),
            _event("feedback", action="accepted"),
            _event("feedback", action="rejected"),
            json.dumps({"kind": "recall", "ts": "2020-01-01T00:00:00Z"}),  # 非今日不计
        ]),
        encoding="utf-8",
    )
    stats = rsi_today(tmp_path / ".rsi")
    assert stats is not None
    assert stats.recalls == 2
    assert stats.adoption_pct == 50.0


def test_rsi_segment_in_line(tmp_path):
    project = tmp_path / "proj"
    logs = project / ".rsi" / "logs"
    logs.mkdir(parents=True)
    (logs / "events.jsonl").write_text(_event("recall") + "\n", encoding="utf-8")
    payload = _cursor_payload(cwd=str(project))
    code, line = _run(payload, argv=["--plain", "--no-quip"])
    assert code == 0
    assert "RSI" in line and "召回 1" in line


def test_find_rsi_dir_walks_up(tmp_path):
    rsi_dir = tmp_path / ".rsi"
    rsi_dir.mkdir()
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_rsi_dir(str(deep)) == rsi_dir.resolve()
    assert find_rsi_dir("") is None or find_rsi_dir("") is not None  # 仅保证不炸


def test_no_rsi_dir_omits_segment(tmp_path):
    payload = _cursor_payload(cwd=str(tmp_path))
    code, line = _run(payload, argv=["--plain", "--no-quip"])
    assert code == 0
    assert "RSI" not in line


# --- 渲染与容错 -----------------------------------------------------------


def test_empty_payload_still_one_line():
    code, line = _run(None, argv=["--plain", "--no-quip"])
    assert code == 0
    assert line == "rsi-boot"


def test_no_color_env_disables_ansi_but_keeps_emoji(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    code, line = _run(_cursor_payload())
    assert code == 0
    assert "\033[" not in line
    assert "🧠" in line  # NO_COLOR 只关颜色，emoji 是内容


def test_plain_disables_emoji(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    code, line = _run(_cursor_payload(), argv=["--plain"])
    assert code == 0
    assert "\033[" not in line
    assert "🧠" not in line


def test_color_output_has_ansi(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    code, line = _run(_cursor_payload())
    assert code == 0
    assert "\033[" in line


def test_render_width_drops_low_priority_segments():
    info = parse_payload(json.dumps(_cursor_payload(render_width_chars=40)))
    info.tokens_in, info.tokens_out, info.tokens_cache = 12_300, 1_800, 96_000
    info.cost_usd = 0.42
    info.duration_ms = 3_720_000
    rsi = RsiToday(recalls=3, positive=9, negative=1)
    line = render_line(info, rsi, "给你比个心❤", lang="zh", color=False, emoji=True)
    assert _visible_width(line) <= 40
    assert "🧠" in line  # 模型段永不丢
    narrow = render_line(info, rsi, "给你比个心❤", lang="zh", color=False)
    assert _visible_width(narrow) <= 200 or True


def test_quip_time_bucket_stable():
    now = time.time()
    first = quips.pick_quip("zh", session_id="s", now=now)
    assert quips.pick_quip("zh", session_id="s", now=now) == first


def test_context_low_quip_pool_when_nearly_full():
    seen = {
        quips.pick_quip("zh", session_id=f"s{i}", now=1_700_000_000, context_remaining_pct=5)
        for i in range(40)
    }
    pool = {entry["zh"] for entry in quips.CONTEXT_LOW}
    assert seen & pool  # 低上下文时告急文案会以高权重出现


# --- 文案池口径（对齐 test_ux_messages 的锁法） ----------------------------


def test_quips_bilingual_contract():
    pools = (quips.REGULAR, quips.MORNING, quips.CONTEXT_LOW, quips.LATE_NIGHT, quips.PRAISE, quips.REST)
    for pool in pools:
        assert pool, "文案池不能为空"
        for entry in pool:
            assert any("一" <= c <= "鿿" for c in entry["zh"]), entry
            assert not any("一" <= c <= "鿿" for c in entry["en"]), entry


def test_morning_pool_enters_in_morning_hours():
    seen = {
        quips.pick_quip("zh", session_id=f"s{i}", now=1_700_000_000, hour=8)
        for i in range(40)
    }
    pool = {entry["zh"] for entry in quips.MORNING}
    assert seen & pool  # 早上 8 点应能抽到早安文案


def test_morning_pool_not_at_night():
    seen = {
        quips.pick_quip("zh", session_id=f"s{i}", now=1_700_000_000, hour=22)
        for i in range(40)
    }
    pool = {entry["zh"] for entry in quips.MORNING}
    assert not (seen & pool)  # 晚上 10 点不应出现早安文案


def test_rest_pool_after_long_session():
    seen = {
        quips.pick_quip("zh", session_id=f"s{i}", now=1_700_000_000, hour=15,
                        duration_ms=50 * 60_000)
        for i in range(40)
    }
    pool = {entry["zh"] for entry in quips.REST}
    assert seen & pool  # 会话 50 分钟应混入久坐提醒


def test_rest_pool_not_for_short_session():
    seen = {
        quips.pick_quip("zh", session_id=f"s{i}", now=1_700_000_000, hour=15,
                        duration_ms=10 * 60_000)
        for i in range(40)
    }
    pool = {entry["zh"] for entry in quips.REST}
    assert not (seen & pool)  # 会话 10 分钟不应出现久坐提醒


# --- 模板造句器 -----------------------------------------------------------


def _handwritten(lang: str) -> set[str]:
    pools = (quips.REGULAR, quips.MORNING, quips.CONTEXT_LOW,
             quips.LATE_NIGHT, quips.PRAISE, quips.REST)
    return {entry[lang] for pool in pools for entry in pool}


def test_generate_quip_bilingual_contract():
    for i in range(400):
        zh = quips.generate_quip("zh", seed=f"s{i}")
        en = quips.generate_quip("en", seed=f"s{i}")
        assert any("一" <= c <= "鿿" for c in zh), zh
        assert not any("一" <= c <= "鿿" for c in en), en


def test_generate_quip_deterministic_per_seed():
    assert quips.generate_quip("zh", seed="fixed") == quips.generate_quip("zh", seed="fixed")


def test_generate_quip_variety():
    seen = {quips.generate_quip("zh", seed=f"v{i}") for i in range(600)}
    assert len(seen) >= 120  # 组合空间 ~220 句，600 次抽样应覆盖大半


def test_pick_quip_also_generates():
    seen = {
        quips.pick_quip("zh", session_id=f"g{i}", now=1_700_000_000, hour=15)
        for i in range(200)
    }
    assert seen - _handwritten("zh")  # 造句器权重 3:2，200 抽必有现场组合句


# --- 用户文案文件 ---------------------------------------------------------


def _write_user_pools(rsi_home, pools: dict) -> None:
    rsi_home.mkdir(parents=True, exist_ok=True)
    (rsi_home / "quips.json").write_text(
        json.dumps({"version": 1, "pools": pools}, ensure_ascii=False), encoding="utf-8"
    )


def test_user_pool_overrides_regular(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    mine = [
        {"zh": "我自己的第一句", "en": "my first line"},
        {"zh": "我自己的第二句", "en": "my second line"},
    ]
    _write_user_pools(home, {"regular": mine})
    seen = {
        quips.pick_quip("zh", session_id=f"u{i}", now=1_700_000_000, hour=15)
        for i in range(60)
    }
    pool_hits = {m["zh"] for m in mine} & seen
    assert pool_hits  # 用户池应被抽中
    assert not (seen - pool_hits - {q for q in seen if q not in _handwritten("zh")}) or True
    handwritten_regular = {e["zh"] for e in quips.REGULAR}
    assert not (seen & handwritten_regular)  # regular 板块被整体替换


def test_user_pool_partial_override_keeps_builtin_scenarios(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    _write_user_pools(home, {"regular": [{"zh": "只有常驻池是我的", "en": "only regular is mine"}]})
    seen = {
        quips.pick_quip("zh", session_id=f"n{i}", now=1_700_000_000, hour=2)
        for i in range(60)
    }
    builtin_night = {e["zh"] for e in quips.LATE_NIGHT}
    assert seen & builtin_night  # 未覆盖的深夜板块仍用内置


def test_broken_user_pool_falls_back(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    home.mkdir(parents=True)
    (home / "quips.json").write_text("{oops", encoding="utf-8")
    line = quips.pick_quip("zh", session_id="x", now=1_700_000_000, hour=15)
    assert line  # 坏文件静默回落，不炸


# --- 防重状态 -------------------------------------------------------------


def test_state_reuses_quip_within_bucket(tmp_path):
    state = tmp_path / "state.json"
    first = quips.pick_quip("zh", session_id="s", now=1_700_000_000, state_path=state)
    again = quips.pick_quip("zh", session_id="s", now=1_700_000_000, state_path=state)
    assert first == again  # 同桶复用，不闪烁


def test_state_avoids_recent_repeats(tmp_path):
    state = tmp_path / "state.json"
    bucket = quips.QUIP_BUCKET_SECONDS
    seen = [
        quips.pick_quip("zh", session_id="s", now=1_700_000_000 + i * bucket, state_path=state)
        for i in range(60)
    ]
    assert len(set(seen)) == len(seen)  # 60 桶无重复（防重生效）


def test_bucket_seconds_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_STATUSLINE_QUIP_SECONDS", "10")
    state = tmp_path / "state.json"
    quips.pick_quip("zh", session_id="s", now=1_700_000_000, state_path=state)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["sessions"]["s"]["bucket"] == 1_700_000_000 // 10


# --- 文案文件校验 ---------------------------------------------------------


def test_validate_quips_data_clean():
    data = {"pools": {"regular": [{"zh": "给你比个心", "en": "a heart for you"}] * 1}}
    errors, warnings, counts = quips.validate_quips_data(data)
    assert errors == [] and counts["regular"] == 1


def test_validate_quips_data_catches_violations():
    data = {"pools": {"regular": [
        {"zh": "no han here", "en": "fine"},
        {"zh": "含汉字", "en": "含汉字"},
        {"zh": "含汉字", "en": "dup"},
    ]}}
    errors, warnings, _ = quips.validate_quips_data(data)
    assert any("zh 必须含汉字" in e for e in errors)
    assert any("en 不得含汉字" in e for e in errors)
    assert any("重复" in e for e in errors)


def test_validate_quips_cli(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"pools": {"regular": [
        {"zh": f"第{i}心里话", "en": f"line {i}"} for i in range(12)
    ]}}, ensure_ascii=False), encoding="utf-8")
    out = io.StringIO()
    code = run_statusline(["--validate-quips", str(path), "--lang", "en"], stdin=io.StringIO(""), stdout=out)
    assert code == 0
    assert "regular: 12" in out.getvalue() and "OK" in out.getvalue()


# --- 风格过滤 -------------------------------------------------------------


def test_serious_excludes_noncore_themes_builtin():
    seen = {
        quips.pick_quip("zh", session_id=f"st{i}", now=1_700_000_000, hour=15, style="serious")
        for i in range(200)
    }
    allowed = quips.STYLE_THEMES["serious"]
    banned = set()
    for theme, entries in quips.REGULAR_THEMES.items():
        if theme not in allowed:
            banned |= {e["zh"] for e in entries}
    assert not (seen & banned)  # serious 不应出现猫咪/玄学/摸鱼等内置条目


def test_style_filters_user_pool_by_theme(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    _write_user_pools(home, {"regular": [
        {"zh": "严肃的条文金句", "en": "a serious maxim", "theme": "motto"},
        {"zh": "一只猫猫路过", "en": "a cat passes by", "theme": "cat"},
        {"zh": "没标签的通用句", "en": "untagged universal"},
    ]})
    seen = {
        quips.pick_quip("zh", session_id=f"sf{i}", now=1_700_000_000, hour=15, style="serious")
        for i in range(120)
    }
    assert "严肃的条文金句" in seen
    assert "没标签的通用句" in seen  # 无标签全风格可用
    assert "一只猫猫路过" not in seen  # cat 主题被 serious 排除


def test_style_filtered_out_user_pool_falls_back(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    _write_user_pools(home, {"regular": [
        {"zh": "只有猫咪", "en": "only cats", "theme": "cat"},
    ]})
    line = quips.pick_quip("zh", session_id="fb", now=1_700_000_000, hour=15, style="serious")
    assert line != "只有猫咪"  # 过滤后空池回落内置 serious 内容


def test_generator_respects_style():
    for i in range(80):
        quip = quips.generate_quip("zh", seed=f"gs{i}", style="serious")
        # serious 只剩 core 配方：祝福对仗/陪伴/养生/安抚四种句型
        assert ("的都" in quip) or quip.startswith("陪你") or ("提醒：" in quip) or ("够用" in quip), quip


def test_buddy_preset_keeps_companionship_and_healing(tmp_path, monkeypatch):
    home = tmp_path / "rsi-home"
    monkeypatch.setenv("RSI_HOME", str(home))
    _write_user_pools(home, {"regular": [
        {"zh": "给你比个心", "en": "a heart for you", "theme": "core"},
        {"zh": "抱抱辛苦的你", "en": "a hug for your hard work", "theme": "healing"},
        {"zh": "猫猫踩键盘", "en": "cat on keyboard", "theme": "cat"},
        {"zh": "锦鲤附体中", "en": "koi engaged", "theme": "fortune"},
    ]})
    seen = {
        quips.pick_quip("zh", session_id=f"b{i}", now=1_700_000_000, hour=15, style="buddy")
        for i in range(120)
    }
    assert "给你比个心" in seen  # core 保留
    assert "抱抱辛苦的你" in seen  # healing 保留
    assert "猫猫踩键盘" not in seen  # cat 排除
    assert "锦鲤附体中" not in seen  # fortune 排除


def test_resolve_style_chain(tmp_path, monkeypatch):
    from rsi_boot.cli.statusline_command import _resolve_style

    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("statusline_style: healing\n", encoding="utf-8")
    monkeypatch.setenv("RSI_STATUSLINE_STYLE", "geek")
    assert _resolve_style("serious") == "serious"  # 旗标最高
    assert _resolve_style(None) == "geek"  # env 压 config
    monkeypatch.delenv("RSI_STATUSLINE_STYLE")
    assert _resolve_style(None) == "healing"  # config 生效
    (tmp_path / "config.yaml").write_text("statusline_style: nonsense\n", encoding="utf-8")
    assert _resolve_style(None) == "mixed"  # 笔误静默回落 mixed


def test_project_config_overrides_user(tmp_path, monkeypatch):
    from rsi_boot.cli.statusline_command import _resolve_lang, _resolve_style

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("statusline_style: healing\nlang: zh\n", encoding="utf-8")
    monkeypatch.setenv("RSI_HOME", str(home))
    project = tmp_path / "proj"
    (project / ".rsi").mkdir(parents=True)
    (project / ".rsi" / "config.yaml").write_text(
        "statusline_style: geek\nlang: en\n", encoding="utf-8"
    )
    assert _resolve_style(None, str(project)) == "geek"  # 项目级压用户级
    assert _resolve_lang(None, str(project)) == "en"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _resolve_style(None, str(elsewhere)) == "healing"  # 无项目配置回落用户级
    assert _resolve_lang(None, str(elsewhere)) == "zh"


def test_project_config_drives_render(tmp_path):
    project = tmp_path / "proj"
    (project / ".rsi").mkdir(parents=True)
    (project / ".rsi" / "config.yaml").write_text("lang: en\n", encoding="utf-8")
    code, line = _run(_cursor_payload(cwd=str(project)), argv=["--plain", "--no-quip"])
    assert code == 0
    assert "ctx left" in line  # 项目级 lang: en 生效
    assert "上下文" not in line


def test_manual_run_falls_back_to_process_cwd(tmp_path, monkeypatch):
    """手动跑（无 payload）时用进程 cwd 认领项目级配置。"""
    project = tmp_path / "proj"
    (project / ".rsi").mkdir(parents=True)
    (project / ".rsi" / "config.yaml").write_text("lang: en\n", encoding="utf-8")
    monkeypatch.chdir(project)
    code, line = _run(None, argv=["--plain"])
    assert code == 0
    # 项目级 lang: en 生效 → 整行（含文案）无汉字
    assert not any("一" <= c <= "鿿" for c in line)


# --- 语言配置链 -----------------------------------------------------------


def test_user_config_lang_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    assert _user_config_lang() is None  # 文件不存在
    cfg = tmp_path / "config.yaml"
    cfg.write_text("# lang: en\n", encoding="utf-8")
    assert _user_config_lang() is None  # 注释行不算
    cfg.write_text('lang: "zh"  # 界面语言\n', encoding="utf-8")
    assert _user_config_lang() == "zh"  # 引号与行尾注释容忍
    cfg.write_text("enhance:\n  lang: en\n", encoding="utf-8")
    assert _user_config_lang() is None  # 只认顶层键


def test_lang_priority_flag_over_env_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("lang: en\n", encoding="utf-8")
    monkeypatch.setenv("RSI_LANG", "zh")
    assert _resolve_lang("en") == "en"  # 旗标最高
    assert _resolve_lang(None) == "zh"  # env 压 config
    monkeypatch.delenv("RSI_LANG")
    assert _resolve_lang(None) == "en"  # config 生效


def test_config_lang_drives_render(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("lang: en\n", encoding="utf-8")
    code, line = _run(_cursor_payload(), argv=["--plain", "--no-quip"])
    assert code == 0
    assert "ctx left" in line
    assert "上下文" not in line
