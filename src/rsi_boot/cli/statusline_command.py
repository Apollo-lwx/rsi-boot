"""rsi statusline：宿主状态栏渲染（Claude Code statusLine / Cursor CLI statusLine 通用）。

宿主每次会话更新都会 spawn 本命令：stdin 喂一个会话 JSON（两宿主 schema 对齐），
stdout 渲染为状态栏文本（单行、ANSI 颜色）。两条硬性约束：

- 快：不构建 runtime、不开 sqlite、不加载索引；只读 transcript 与 events 尾部；
- 稳：永远 exit 0 且至少输出一行（非零 + 空 stdout 会让宿主保留旧文本）。

数据来源：
- Cursor CLI：payload.context_window 直接给 used/remaining 百分比、窗口大小与输入 token；
- Claude Code：解析 transcript_path 的 JSONL，取最后一条主链 assistant 消息的 usage
  算上下文占用（input + cache_read + cache_creation），累计三类 token 做会话统计；
  cost / duration 来自 payload.cost；
- RSI 段：从 cwd 向上找 .rsi/logs/events.jsonl，读尾部统计今日召回与采纳；
  找不到就静默省略——状态栏永远不报错。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t as _t
from rsi_boot.ux.quips import (
    STYLE_NAMES,
    pick_quip,
    quip_state_path,
    user_pools_path,
    validate_quips_data,
)

#: stdin 读取上限（防御异常宿主）
_MAX_STDIN = 2 * 1024 * 1024
#: transcript 全量解析上限；超过则只读尾部 _TAIL_BYTES 并把 token 合计标为约数
_WHOLE_BYTES = 16 * 1024 * 1024
_TAIL_BYTES = 4 * 1024 * 1024
#: events.jsonl 只读尾部这么多来估今日统计
_EVENTS_TAIL = 512 * 1024
#: 从 cwd 向上找 .rsi 的层数上限
_ASCEND_LIMIT = 6
#: Claude Code 默认上下文窗口；超出 200k 的会话按 1M 档；可用环境变量覆盖
_DEFAULT_CTX_WINDOW = 200_000
_LARGE_CTX_WINDOW = 1_000_000

_POSITIVE_ACTIONS = frozenset({"accepted", "applied", "copied", "referenced"})
_NEGATIVE_ACTIONS = frozenset({"rejected", "modified"})

#: 用户级语言键：~/.rsi/config.yaml 顶层的 `lang: zh|en`（轻量行扫，不引 yaml 拖慢冷启动）
_CONFIG_LANG_RE = re.compile(r"^lang\s*:\s*['\"]?(zh|en)['\"]?\s*(?:#.*)?$", re.MULTILINE)
#: 用户级风格键：~/.rsi/config.yaml 顶层的 `statusline_style: mixed|serious|healing|geek|lucky`
_CONFIG_STYLE_RE = re.compile(
    r"^statusline_style\s*:\s*['\"]?([A-Za-z_]+)['\"]?\s*(?:#.*)?$", re.MULTILINE
)


def _config_text(path: Path, pattern: re.Pattern[str]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = pattern.search(text)
    return match.group(1) if match else None


def _user_config_text(pattern: re.Pattern[str]) -> str | None:
    """用户级：~/.rsi/config.yaml（RSI_HOME 可覆盖）"""
    home = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi")))
    return _config_text(home / "config.yaml", pattern)


def _project_config_text(cwd: str, pattern: re.Pattern[str]) -> str | None:
    """项目级：<工作区>/.rsi/config.yaml（从 cwd 向上认领 .rsi；项目级优先于用户级）"""
    rsi_dir = find_rsi_dir(cwd)
    if rsi_dir is None:
        return None
    return _config_text(rsi_dir / "config.yaml", pattern)


def _user_config_lang() -> str | None:
    return _user_config_text(_CONFIG_LANG_RE)


def _resolve_style(flag: str | None, cwd: str = "") -> str:
    """风格链：--style → RSI_STATUSLINE_STYLE → 项目 .rsi/config.yaml → 用户 config.yaml → mixed。
    未识别的值静默回落 mixed（状态栏不因配置笔误罢工）。"""
    if flag in STYLE_NAMES:
        return flag
    env = (os.environ.get("RSI_STATUSLINE_STYLE") or "").strip().lower()
    if env in STYLE_NAMES:
        return env
    for configured in (
        _project_config_text(cwd, _CONFIG_STYLE_RE),
        _user_config_text(_CONFIG_STYLE_RE),
    ):
        if configured and configured.strip().lower() in STYLE_NAMES:
            return configured.strip().lower()
    return "mixed"


def _resolve_lang(flag: str | None, cwd: str = "") -> str:
    """语言链：--lang → RSI_LANG → 项目 .rsi/config.yaml → 用户 config.yaml → 系统语言。"""
    if flag in ("zh", "en"):
        return flag
    env = (os.environ.get("RSI_LANG") or "").strip().lower()
    if env in ("zh", "en"):
        return env
    for configured in (
        _project_config_text(cwd, _CONFIG_LANG_RE),
        _user_config_lang(),
    ):
        if configured in ("zh", "en"):
            return configured
    return locale_lang()

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_COLORS = {
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
    "dim": "\033[90m",
    "reset": "\033[0m",
}


@dataclass
class SessionInfo:
    """两宿主 stdin payload 归一化后的会话视图。"""

    model: str = ""
    cwd: str = ""
    session_id: str = ""
    transcript_path: str = ""
    cost_usd: float | None = None
    duration_ms: float | None = None
    ctx_used_pct: float | None = None
    ctx_window: int | None = None
    render_width: int | None = None
    exceeds_200k: bool = False
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None
    tokens_approx: bool = False

    @property
    def ctx_remaining_pct(self) -> float | None:
        if self.ctx_used_pct is None:
            return None
        return max(0.0, 100.0 - self.ctx_used_pct)


@dataclass
class RsiToday:
    recalls: int = 0
    positive: int = 0
    negative: int = 0

    @property
    def adoption_pct(self) -> float | None:
        total = self.positive + self.negative
        if total == 0:
            return None
        return self.positive / total * 100

    @property
    def empty(self) -> bool:
        return self.recalls == 0 and self.positive == 0 and self.negative == 0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_payload(text: str) -> SessionInfo:
    """归一化 Cursor CLI / Claude Code 的 stdin JSON；垃圾输入返回空视图。"""
    info = SessionInfo()
    if not text:
        return info
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return info
    if not isinstance(obj, dict):
        return info

    model = obj.get("model")
    if isinstance(model, dict):
        info.model = str(model.get("display_name") or model.get("id") or "")
    elif isinstance(model, str):
        info.model = model
    workspace = obj.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    info.cwd = str(obj.get("cwd") or workspace.get("current_dir") or "")
    info.session_id = str(obj.get("session_id") or "")
    info.transcript_path = str(obj.get("transcript_path") or "")

    cost = obj.get("cost")
    if isinstance(cost, dict):
        info.cost_usd = _as_float(cost.get("total_cost_usd"))
        info.duration_ms = _as_float(cost.get("total_duration_ms"))

    window = obj.get("context_window")
    if isinstance(window, dict):
        used = _as_float(window.get("used_percentage"))
        remaining = _as_float(window.get("remaining_percentage"))
        if used is not None:
            info.ctx_used_pct = used
        elif remaining is not None:
            info.ctx_used_pct = max(0.0, 100.0 - remaining)
        ctx_size = _as_int(window.get("context_window_size"))
        if ctx_size > 0:
            info.ctx_window = ctx_size
        tokens_in = _as_int(window.get("total_input_tokens"))
        if tokens_in > 0:
            info.tokens_in = tokens_in
            info.tokens_approx = True  # 宿主由 used_percentage 反推，本来就是估算
        tokens_out = _as_int(window.get("total_output_tokens"))
        if tokens_out > 0:
            info.tokens_out = tokens_out

    info.exceeds_200k = bool(obj.get("exceeds_200k_tokens"))
    width = _as_int(obj.get("render_width_chars"))
    if width > 0:
        info.render_width = width
    return info


def _default_window(info: SessionInfo) -> int:
    raw = os.environ.get("RSI_STATUSLINE_CTX_WINDOW", "").strip()
    override = _as_int(raw)
    if override > 0:
        return override
    return _LARGE_CTX_WINDOW if info.exceeds_200k else _DEFAULT_CTX_WINDOW


def _usage_from(obj: dict[str, Any]) -> dict[str, Any] | None:
    message = obj.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return message["usage"]
    if isinstance(obj.get("usage"), dict):
        return obj["usage"]
    return None


def scan_transcript(info: SessionInfo) -> None:
    """解析 transcript JSONL：累计 token 合计；payload 没给上下文百分比时用最后一条
    主链 assistant usage 推算。只处理含 "usage" 的行，大文件只读尾部。"""
    if not info.transcript_path:
        return
    path = Path(info.transcript_path)
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= 0:
        return
    approx = False
    try:
        if size <= _WHOLE_BYTES:
            data = path.read_bytes()
        else:
            approx = True
            with path.open("rb") as fh:
                fh.seek(size - _TAIL_BYTES)
                data = fh.read(_TAIL_BYTES)
    except OSError:
        return

    tokens_in = tokens_out = tokens_cache = 0
    last_ctx: int | None = None
    for line in data.decode("utf-8", errors="replace").splitlines():
        if '"usage"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        usage = _usage_from(obj)
        if usage is None:
            continue
        i = _as_int(usage.get("input_tokens"))
        o = _as_int(usage.get("output_tokens"))
        cr = _as_int(usage.get("cache_read_input_tokens"))
        cc = _as_int(usage.get("cache_creation_input_tokens"))
        tokens_in += i
        tokens_out += o
        tokens_cache += cr + cc
        if not obj.get("isSidechain"):
            last_ctx = i + cr + cc

    if tokens_in or tokens_out or tokens_cache:
        info.tokens_in = tokens_in
        info.tokens_out = tokens_out
        info.tokens_cache = tokens_cache
        info.tokens_approx = approx
    if info.ctx_used_pct is None and last_ctx:
        window = info.ctx_window or _default_window(info)
        info.ctx_window = window
        info.ctx_used_pct = min(100.0, last_ctx / window * 100)


def find_rsi_dir(cwd: str) -> Path | None:
    """RSI_PROJECT_ROOT 优先，其次从 cwd 向上找 .rsi/（有层数上限）。"""
    candidates: list[Path] = []
    env_root = os.environ.get("RSI_PROJECT_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    if cwd:
        candidates.append(Path(cwd))
    for start in candidates:
        try:
            current = start.resolve()
        except OSError:
            continue
        for _ in range(_ASCEND_LIMIT):
            rsi_dir = current / ".rsi"
            if rsi_dir.is_dir():
                return rsi_dir
            if current.parent == current:
                break
            current = current.parent
    return None


def rsi_today(rsi_dir: Path) -> RsiToday | None:
    """读 events.jsonl 尾部，统计今日（UTC）召回次数与反馈采纳；全零返回 None。"""
    path = rsi_dir / "logs" / "events.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    try:
        if size <= _EVENTS_TAIL:
            data = path.read_bytes()
        else:
            with path.open("rb") as fh:
                fh.seek(size - _EVENTS_TAIL)
                data = fh.read(_EVENTS_TAIL)
    except OSError:
        return None

    today = time.strftime("%Y-%m-%d", time.gmtime())
    stats = RsiToday()
    for line in data.decode("utf-8", errors="replace").splitlines():
        # 先字符串预筛，避免每行都 json.loads
        if '"kind"' not in line or today not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if not str(obj.get("ts") or "").startswith(today):
            continue
        kind = obj.get("kind")
        if kind == "recall" or str(kind or "").startswith("recall"):
            stats.recalls += 1
        elif kind == "feedback":
            action = str(obj.get("action") or "")
            if action in _POSITIVE_ACTIONS:
                stats.positive += 1
            elif action in _NEGATIVE_ACTIONS:
                stats.negative += 1
    return None if stats.empty else stats


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_duration(ms: float, lang: str) -> str:
    seconds = int(ms / 1000)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return _t("HOUR", lang, n=hours) + _t("MIN", lang, n=minutes)
    if minutes:
        return _t("MIN", lang, n=minutes)
    return _t("SEC", lang, n=seconds)


def _c(text: str, color: str, enable: bool) -> str:
    if not enable:
        return text
    return f"{_COLORS[color]}{text}{_COLORS['reset']}"


def _visible_width(text: str) -> int:
    """去掉 ANSI 后的显示宽度：CJK/emoji 记 2，其余记 1（近似足够）。"""
    plain = _ANSI_RE.sub("", text)
    return sum(2 if ord(ch) >= 0x2E80 else 1 for ch in plain)


def render_line(
    info: SessionInfo,
    rsi: RsiToday | None,
    quip: str,
    *,
    lang: str,
    color: bool = True,
    emoji: bool = True,
) -> str:
    """拼单行状态栏；给了 render_width 时按优先级从低到高丢段落直到放得下。"""
    e = lambda icon: f"{icon} " if emoji else ""  # noqa: E731
    segments: list[tuple[int, str]] = []  # (drop_priority, text) drop_priority 越大越先丢

    segments.append((0, _c(f"{e('🧠')}{info.model or 'rsi-boot'}", "cyan", color)))

    remaining = info.ctx_remaining_pct
    if remaining is not None:
        tone = "green" if remaining > 40 else ("yellow" if remaining > 15 else "red")
        ctx_text = (
            f"上下文余 {remaining:.0f}%" if lang == "zh" else f"ctx left {remaining:.0f}%"
        )
        segments.append((1, _c(f"{e('📊')}{ctx_text}", tone, color)))

    if quip:
        segments.append((2, _c(quip, "magenta", color)))

    if info.tokens_in is not None:
        mark = "≈" if info.tokens_approx else ""
        if info.tokens_out is not None and info.tokens_cache is not None:
            token_text = (
                f"in {mark}{_fmt_tokens(info.tokens_in)} · "
                f"out {mark}{_fmt_tokens(info.tokens_out)} · "
                f"cache {mark}{_fmt_tokens(info.tokens_cache)}"
            )
        else:
            token_text = f"tokens {mark}{_fmt_tokens(info.tokens_in)}"
        segments.append((3, _c(f"{e('⛽')}{token_text}", "dim", color)))

    if rsi is not None:
        parts = []
        if rsi.recalls:
            parts.append(f"召回 {rsi.recalls}" if lang == "zh" else f"recalls {rsi.recalls}")
        adoption = rsi.adoption_pct
        if adoption is not None:
            parts.append(f"采纳 {adoption:.0f}%" if lang == "zh" else f"adopted {adoption:.0f}%")
        if parts:
            segments.append((4, _c(f"{e('🐾')}RSI {' · '.join(parts)}", "dim", color)))

    if info.cost_usd is not None:
        segments.append((5, _c(f"{e('💰')}${info.cost_usd:.2f}", "dim", color)))

    if info.duration_ms is not None and info.duration_ms > 0:
        segments.append((6, _c(f"{e('⏱')}{_fmt_duration(info.duration_ms, lang)}", "dim", color)))

    joiner = " │ "
    while len(segments) > 1:
        line = joiner.join(text for _, text in segments)
        if info.render_width is None or _visible_width(line) <= info.render_width:
            return line
        worst = max(range(len(segments)), key=lambda idx: (segments[idx][0], idx))
        segments.pop(worst)
    return segments[0][1] if segments else "rsi-boot"


def _read_payload(stream: TextIO) -> str:
    try:
        if stream is sys.stdin and sys.stdin.isatty():
            return ""  # 人手在终端里试跑时不阻塞
        return stream.read(_MAX_STDIN) or ""
    except (OSError, ValueError):
        return ""


def _run_validate_quips(args: argparse.Namespace, out: TextIO) -> int:
    """--validate-quips：校验用户文案文件并打印报告；错误 → 退出码 1。"""
    lang = _resolve_lang(args.lang)
    zh = lang == "zh"
    path = Path(args.validate_quips) if args.validate_quips else user_pools_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{'读不了文案文件' if zh else 'cannot read quips file'}: {path} ({exc})", file=out)
        return 1
    errors, warnings, counts = validate_quips_data(data)
    total = sum(counts.values())
    for name, count in counts.items():
        print(f"  {name}: {count}", file=out)
    print(f"{'合计' if zh else 'total'}: {total} ({path})", file=out)
    for warning in warnings:
        print(f"{'警告' if zh else 'warning'}: {warning}", file=out)
    for error in errors:
        print(f"{'错误' if zh else 'error'}: {error}", file=out)
    if errors:
        print(f"{'校验失败' if zh else 'validation failed'}: {len(errors)}", file=out)
        return 1
    print("OK" if not warnings else ("通过（有警告）" if zh else "passed with warnings"), file=out)
    return 0


def run_statusline(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="rsi statusline")
    parser.add_argument("--lang", choices=["zh", "en"], default=None)
    parser.add_argument("--no-rsi", action="store_true", help="不显示 RSI 统计段")
    parser.add_argument("--no-quip", action="store_true", help="不显示随机文案")
    parser.add_argument("--plain", action="store_true", help="禁用颜色与 emoji")
    parser.add_argument("--style", choices=list(STYLE_NAMES), default=None,
                        help="文案风格（默认 RSI_STATUSLINE_STYLE / config.yaml / mixed）")
    parser.add_argument("--validate-quips", nargs="?", const="", default=None, metavar="PATH",
                        help="校验用户文案文件（默认 ~/.rsi/quips.json）后退出")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise  # --help 正常透出
        args = argparse.Namespace(lang=None, no_rsi=False, no_quip=False, plain=False,
                                  style=None, validate_quips=None)

    stream = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout

    if args.validate_quips is not None:
        return _run_validate_quips(args, out)

    try:
        info = parse_payload(_read_payload(stream))
        scan_transcript(info)
        if not info.cwd:
            # 手动冒烟（TTY 无 payload）时用进程 cwd 兜底，项目级配置/RSI 段才能生效
            info.cwd = os.getcwd()
        lang = _resolve_lang(args.lang, info.cwd)
        style = _resolve_style(args.style, info.cwd)

        rsi: RsiToday | None = None
        if not args.no_rsi:
            rsi_dir = find_rsi_dir(info.cwd)
            if rsi_dir is not None:
                rsi = rsi_today(rsi_dir)

        quip = ""
        if not args.no_quip:
            quip = pick_quip(
                lang,
                session_id=info.session_id,
                context_remaining_pct=info.ctx_remaining_pct,
                adoption_rate=rsi.adoption_pct if rsi else None,
                recalls=rsi.recalls if rsi else 0,
                duration_ms=info.duration_ms,
                state_path=quip_state_path(),
                style=style,
            )

        # NO_COLOR 按惯例只关 ANSI 颜色；emoji 属内容，仅 --plain 才关
        color = not args.plain and not os.environ.get("NO_COLOR")
        line = render_line(info, rsi, quip, lang=lang, color=color, emoji=not args.plain)
    except Exception:
        line = "rsi-boot"  # 状态栏永不出错：兜底一行
    print(line, file=out)
    return 0
