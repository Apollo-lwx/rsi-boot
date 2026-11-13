"""tool-choice 案例集离线校验（借鉴 OpenMausBot evals/tool-choice）。

OpenMausBot 用真实模型逐案例跑（live opt-in）；rsi-boot 本地零 key，
离线层做三件可 CI 的事：

1. schema 完整：id 唯一、expect 非空、expect ∩ misuse = ∅；
2. 注册表一致：expect 工具与 rsi_* 形式的 misuse 必须在 MCP 注册清单内；
3. 描述哨兵：每条案例的 trigger_terms 必须出现在对应工具的实时描述里——
   改工具描述删掉锚点词，本检查即红（「改描述前后必跑」的离线近似）。

用法：
  python -X utf8 benchmark/tool-choice/check.py            # 校验，非零退出=失败
  python -X utf8 benchmark/tool-choice/check.py --report   # 追加写 reports/tool-choice/SUMMARY.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.yaml"
REPORT_PATH = HERE.parent.parent / "benchmark" / "results" / "tool-choice" / "SUMMARY.md"

_MIN_CASES = 20


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 顶层必须是案例列表")
    return [c for c in data if isinstance(c, dict)]


def registered_tools() -> dict[str, str]:
    """MCP 注册工具名 → 实时描述文本。"""
    from rsi_boot.api.tools import (
        conflicts_tool,
        explore_tool,
        feedback_tool,
        impact_tool,
        knowledge_add_tool,
        knowledge_delete_tool,
        knowledge_review_tool,
        knowledge_search_tool,
        knowledge_update_tool,
        learn_tool,
        memory_health_tool,
        memory_tool,
        recall_tool,
        review_tool,
        stats_tool,
        web_ui_tool,
    )
    from rsi_boot.ux.messages import TOOL_DESC

    modules = [
        (recall_tool, TOOL_DESC["recall"]),
        (explore_tool, TOOL_DESC["explore"]),
        (feedback_tool, TOOL_DESC["feedback"]),
        (knowledge_add_tool, TOOL_DESC["knowledge_add"]),
        (knowledge_search_tool, TOOL_DESC["knowledge_search"]),
        (knowledge_delete_tool, TOOL_DESC["knowledge_delete"]),
        (knowledge_update_tool, TOOL_DESC["knowledge_update"]),
        (knowledge_review_tool, TOOL_DESC["review"]),
        (review_tool, TOOL_DESC["harness"]),
        (conflicts_tool, TOOL_DESC["conflicts"]),
        (stats_tool, TOOL_DESC["stats"]),
        (impact_tool, TOOL_DESC["impact"]),
        (learn_tool, TOOL_DESC["learn"]),
        (memory_health_tool, TOOL_DESC["memory_health"]),
        (memory_tool, TOOL_DESC["memory"]),
        (web_ui_tool, TOOL_DESC["web_ui"]),
    ]
    return {m.TOOL_NAME: desc for m, desc in modules}


def check_cases(cases: list[dict[str, Any]], registry: dict[str, str] | None = None) -> list[str]:
    """返回错误列表；空列表 = 全过。"""
    registry = registry if registry is not None else registered_tools()
    errors: list[str] = []
    if len(cases) < _MIN_CASES:
        errors.append(f"案例数 {len(cases)} < {_MIN_CASES}（方案底线）")

    seen: set[str] = set()
    for case in cases:
        cid = str(case.get("id") or "<missing>")
        if cid in seen:
            errors.append(f"{cid}: id 重复")
        seen.add(cid)
        for field in ("text", "expect", "why", "source"):
            if not case.get(field):
                errors.append(f"{cid}: 缺字段 {field}")
        expect = [str(t) for t in (case.get("expect") or [])]
        misuse = [str(m) for m in (case.get("misuse") or [])]
        if not expect:
            errors.append(f"{cid}: expect 为空")
        overlap = set(expect) & set(misuse)
        if overlap:
            errors.append(f"{cid}: expect 与 misuse 重叠 {sorted(overlap)}")
        for name in expect:
            if name not in registry:
                errors.append(f"{cid}: expect 工具 {name} 未注册")
        for name in misuse:
            if name.startswith("rsi_") and name not in registry:
                errors.append(f"{cid}: misuse 工具 {name} 未注册")
        triggers = case.get("trigger_terms") or {}
        if not isinstance(triggers, dict):
            errors.append(f"{cid}: trigger_terms 必须是映射")
            continue
        for tool, terms in triggers.items():
            desc = registry.get(str(tool))
            if desc is None:
                errors.append(f"{cid}: trigger_terms 引用未注册工具 {tool}")
                continue
            for term in terms if isinstance(terms, list) else [terms]:
                if str(term) not in desc:
                    errors.append(f"{cid}: 锚点词 {term!r} 不在 {tool} 的描述中")
    return errors


_REPORT_TEMPLATE = """# tool-choice 误用案例集报告

## 数据口径

- 案例集：`benchmark/tool-choice/cases.yaml`，{n_cases} 条真实误用案例
  （出处为每条 source 字段：本仓库 .rsi/memory 教学案例 / AGENTS.md 托管块纪律）。
- 离线校验（本报告）：schema 完整性、工具注册表一致性、描述锚点词哨兵。
  复跑：`python -X utf8 benchmark/tool-choice/check.py --report`。
- 在线层（真实宿主模型逐案例跑）未实施：rsi-boot 本地零 key，参照
  OpenMausBot 的 OMB_EVAL_LIVE 做法留作宿主侧 opt-in。

## 数字

- 案例数：{n_cases}（方案底线 ≥ 20）
- 校验错误：{n_errors}
{error_block}

## 能证明什么

- 案例集结构完整、与当前工具注册表和实时工具描述一致。
- 工具描述删除任一锚点词会被校验捕获（描述回归哨兵在位）。

## 不能证明什么

- 未评估真实模型的工具选择准确率——离线校验只保证案例与描述一致，
  不证明宿主模型在案例场景下会选对工具。
- 样本外推广不支撑：22 条案例全部来自本仓库自身教训，覆盖不了其它项目形态。
- 环境差异未评估：不同宿主（Cursor/Qoder/Codex）对同一工具描述的遵循度未测量。

## 变异测试记录

| 变异（改坏的行） | 应变红的检查 | 结果 |
|---|---|---|
| cases.yaml 写入描述中不存在的锚点词（'recall'） | `锚点词不在描述中` 报错 | 实测红（explore-then-recall-detail），已修正为 'feedback_token' |
| 注册表描述整体清空 | 锚点词检查全红 | tests/test_tool_choice_eval.py::test_trigger_terms_catch_description_regression 值守 |
| cases.yaml 案例数减到 19 | `案例数 < 20` 报错 | tests/test_tool_choice_eval.py::test_cases_meet_minimum 值守 |
"""


def write_report(cases: list[dict[str, Any]], errors: list[str]) -> Path:
    error_block = "\n".join(f"- {e}" for e in errors) if errors else "- 无"
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        _REPORT_TEMPLATE.format(
            n_cases=len(cases), n_errors=len(errors), error_block=error_block,
        ),
        encoding="utf-8",
    )
    return REPORT_PATH


def main(argv: list[str]) -> int:
    cases = load_cases()
    errors = check_cases(cases)
    if "--report" in argv:
        path = write_report(cases, errors)
        print(f"报告 -> {path}")
    if errors:
        for e in errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 1
    print(f"OK {len(cases)} cases, registry {len(registered_tools())} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
