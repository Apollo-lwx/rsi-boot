from pathlib import Path

from rsi_boot.scanner.config_scanner import scan_configs
from rsi_boot.scanner.profile_generator import build_profile


def test_java_majority_does_not_steal_pydantic_as_sole_framework(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pom.xml").write_text(
        "<project><artifactId>junit-jupiter</artifactId></project>", encoding="utf-8",
    )
    java = [tmp_path / f"A{i}.java" for i in range(6)]
    py = [tmp_path / "a.py"]
    for p in java + py:
        p.write_text("class A {}", encoding="utf-8")
    insights = scan_configs(
        tmp_path,
        [tmp_path / "pyproject.toml", tmp_path / "pom.xml"],
        [], [], java + py,
    )
    assert "java" in (insights.language or "")
    assert "python" in (insights.language or "")
    profile = build_profile("u", "local", insights, "moderate", "weak")
    fw = profile.preferences.role_specific.get("framework", "")
    assert "pydantic" in fw
    assert "java" in (insights.language or "")
    assert fw != "pydantic"  # 不得再单独冒充全局框架
