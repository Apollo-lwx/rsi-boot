"""Python 项目配置解析——requirements.txt 支持 + pytest.ini 兜底。

实仓验证（CalciteTest）发现：仓库用 requirements.txt 声明依赖（pytest==7.4.3 等）
且根目录有 pytest.ini，但画像 test_framework/build_system 全空——config_scanner
只认 pyproject.toml，signal_discovery 也不把 requirements.txt 当配置信号。
"""

import json
from collections import Counter
from pathlib import Path

from rsi_boot.scanner.config_scanner import ConfigInsights, scan_configs
from rsi_boot.scanner.profile_generator import build_profile
from rsi_boot.scanner.signal_discovery import discover_signals

_REQUIREMENTS = """# Hive SQL 自动化测试框架依赖

# 核心依赖
pyhive==0.7.0
thrift==0.16.0
sasl==0.3.1

# 测试框架
pytest==7.4.3
pytest-html==4.1.1
pytest-cov>=4.0
pytest-xdist

# 配置管理
pyyaml==6.0.1
uvicorn[standard]>=0.30
requests; python_version >= "3.8"

-r requirements-dev.txt
-e .
--index-url https://pypi.example.com/simple
"""


def test_requirements_txt_parsed(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(_REQUIREMENTS, encoding="utf-8")
    insights = scan_configs(tmp_path, [req], [], [], [])
    assert insights.language == "python"
    assert insights.build_system == "pip"
    assert insights.test_framework == "pytest"
    # 注释/空行/-r/-e/--option 行不计入；uvicorn[standard]、requests; marker 归一化为包名
    assert insights.dependencies_count == 10


def test_requirements_variants_matched(tmp_path):
    """requirements-dev.txt / requirements_test.txt 等变体同样识别"""
    req = tmp_path / "requirements-dev.txt"
    req.write_text("flask>=2.0\npytest\n", encoding="utf-8")
    insights = scan_configs(tmp_path, [req], [], [], [])
    assert insights.language == "python"
    assert insights.build_system == "pip"
    assert insights.framework == "flask"
    assert insights.test_framework == "pytest"


def test_pytest_ini_fallback_without_tests_dir(tmp_path):
    """无 tests/ 目录但存在 pytest.ini → 推断 pytest（语言由代码文件判定）"""
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = sql_test_framework\n", encoding="utf-8")
    src = tmp_path / "pkg"
    src.mkdir()
    code = [src / "a.py", src / "b.py"]
    for p in code:
        p.write_text("x = 1\n", encoding="utf-8")
    insights = scan_configs(tmp_path, [tmp_path / "pytest.ini"], [], [], code)
    assert insights.language == "python"
    assert insights.test_framework == "pytest"


def test_signal_discovery_finds_requirements(tmp_path):
    """requirements.txt 应被发现为 config 信号（当前只命中 docs）"""
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    signals = discover_signals(tmp_path)
    assert tmp_path / "requirements.txt" in signals["config"].files


# ---------- dependency_names → expertise 种子（小项目 import 计数达不到阈值） ----------


def test_dependency_names_collected(tmp_path):
    """requirements.txt 依赖名进入 ConfigInsights.dependency_names（归一化、去选项行）"""
    req = tmp_path / "requirements.txt"
    req.write_text(_REQUIREMENTS, encoding="utf-8")
    insights = scan_configs(tmp_path, [req], [], [], [])
    names = insights.dependency_names
    assert "pyhive" in names and "pyyaml" in names and "uvicorn" in names
    assert "requests" in names
    assert not any(n.startswith("-") or n.startswith(".") for n in names)


def test_package_json_dependency_names(tmp_path):
    """package.json 的 dependencies/devDependencies 同样供给 dependency_names"""
    pkg = tmp_path / "package.json"
    pkg.write_text(
        json.dumps({"dependencies": {"vue": "^3.0.0"}, "devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )
    insights = scan_configs(tmp_path, [pkg], [], [], [])
    assert "vue" in insights.dependency_names
    assert "vitest" in insights.dependency_names


def test_profile_expertise_seeded_from_dependencies():
    """import 计数低于阈值时，清单依赖名补充 expertise；stdlib 仍过滤"""
    insights = ConfigInsights(language="python", build_system="pip", test_framework="pytest")
    insights.dependency_names = ["pyhive", "pyyaml", "pytest", "os", "subprocess"]
    profile = build_profile(
        "u1", "p1", insights, "moderate", "moderate",
        import_counter=Counter({"yaml": 3}),  # 低于阈值 10，不产生标签
    )
    assert "pyhive" in profile.expertise
    assert "pyyaml" in profile.expertise
    assert "os" not in profile.expertise and "subprocess" not in profile.expertise


def test_profile_expertise_import_priority_and_dedup():
    """import 统计（≥阈值）在前，依赖名补充在后；同名去重"""
    insights = ConfigInsights(language="python", build_system="pip")
    insights.dependency_names = ["requests", "pydantic", "faker"]
    profile = build_profile(
        "u1", "p1", insights, "moderate", "moderate",
        import_counter=Counter({"requests": 25}),
    )
    assert profile.expertise[0] == "requests"           # import 强信号优先
    assert profile.expertise.count("requests") == 1     # 去重
    assert "pydantic" in profile.expertise and "faker" in profile.expertise
