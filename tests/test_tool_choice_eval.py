"""tool-choice 案例集 CI 门禁（方案任务 1.4）。

把 benchmark/tool-choice/check.py 的三方一致性校验纳入测试套件：
案例结构、工具注册表、描述锚点词。案例数跌破 20 底线即红。

变异点：cases.yaml 删锚点词 / 减案例 / expect 写错工具名，对应断言全红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "benchmark" / "tool-choice"
sys.path.insert(0, str(BENCH))

import check  # noqa: E402


@pytest.fixture(scope="module")
def cases():
    return check.load_cases()


def test_cases_meet_minimum(cases):
    assert len(cases) >= 20, f"案例数 {len(cases)} 跌破方案底线 20"


def test_cases_consistent_with_registry(cases):
    errors = check.check_cases(cases)
    assert errors == [], "；".join(errors[:5])


def test_trigger_terms_catch_description_regression(cases):
    """描述哨兵自证：伪造一个缺锚点词的注册表，校验必须报错。"""
    registry = check.registered_tools()
    broken = {name: "" for name in registry}  # 清空全部描述
    errors = check.check_cases(cases, registry=broken)
    assert any("锚点词" in e for e in errors)


def test_report_written(cases, tmp_path, monkeypatch):
    out = tmp_path / "SUMMARY.md"
    monkeypatch.setattr(check, "REPORT_PATH", out)
    check.write_report(cases, [])
    text = out.read_text(encoding="utf-8")
    assert "## 不能证明什么" in text  # 0.3 证据边界纪律
    assert "## 数据口径" in text
