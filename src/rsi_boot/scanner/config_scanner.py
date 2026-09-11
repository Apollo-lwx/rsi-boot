"""配置/规范解析器（§10.9.2）：识别技术栈、构建工具、测试框架、规范规则。

核心层范围：pyproject.toml / package.json / .editorconfig / Makefile / CI 存在性。
解析失败即跳过（§10.9.7 格式完整性），不阻塞其他信号。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# 常见依赖 → 框架标签（用于画像 framework 字段）
_FRAMEWORK_HINTS = {
    "django": "django", "flask": "flask", "fastapi": "fastapi", "react": "react",
    "vue": "vue", "express": "express", "spring-boot": "spring", "next": "nextjs",
    "pydantic": "pydantic", "mcp": "mcp",
}
_TEST_FRAMEWORKS = ("pytest", "jest", "vitest", "mocha", "junit", "go test")

# JVM artifactId 子串 → 框架标签（spring-boot-starter-web 命中 spring-boot）
_POM_FRAMEWORK_HINTS = {
    "spring-boot": "spring", "spring-cloud": "spring", "springframework": "spring",
    "mybatis": "mybatis", "quarkus": "quarkus", "micronaut": "micronaut",
    "dropwizard": "dropwizard", "vert.x": "vertx",
}
#: artifactId（前缀匹配）→ 测试框架标签；spring-boot-starter-test 聚合 JUnit5/Mockito/AssertJ
_POM_TEST_FRAMEWORKS = {"junit": "junit", "testng": "testng",
                        "spring-boot-starter-test": "junit"}


@dataclass
class ConfigInsights:
    language: Optional[str] = None
    framework: Optional[str] = None
    build_system: Optional[str] = None
    test_framework: Optional[str] = None
    ci_pipeline: Optional[str] = None
    conventions: List[str] = field(default_factory=list)
    dependencies_count: int = 0
    #: 清单声明的依赖名（归一化小写，供画像 expertise 种子；pom 不收集——
    #: artifactId 含项目自身坐标，噪声大）
    dependency_names: List[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = ["# 项目配置与规范摘要"]
        for label, value in (
            ("主语言", self.language), ("框架", self.framework),
            ("构建系统", self.build_system), ("测试框架", self.test_framework),
            ("CI", self.ci_pipeline),
        ):
            if value:
                lines.append(f"- {label}: {value}")
        if self.conventions:
            lines.append(f"- 规范文件: {', '.join(self.conventions)}")
        return "\n".join(lines)


def _parse_pyproject(path: Path, insights: ConfigInsights) -> None:
    try:
        import tomllib
        data: Dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("pyproject.toml 解析失败，跳过: %s", exc)
        return
    project = data.get("project", {})
    deps = list(project.get("dependencies", []))
    deps += [d for group in project.get("optional-dependencies", {}).values() for d in group]
    insights.dependencies_count += len(deps)
    dep_names = {re.split(r"[<>=!~\[]", d, 1)[0].strip().lower() for d in deps}
    insights.dependency_names.extend(sorted(dep_names))

    for dep, label in _FRAMEWORK_HINTS.items():
        if dep in dep_names:
            insights.framework = insights.framework or label
    for tf in _TEST_FRAMEWORKS:
        if tf.split()[0] in dep_names:
            insights.test_framework = insights.test_framework or tf
    build = data.get("build-system", {}).get("build-backend", "")
    if build:
        insights.build_system = build.split(".")[0]
    insights.language = insights.language or "python"


def _parse_package_json(path: Path, insights: ConfigInsights) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("package.json 解析失败，跳过: %s", exc)
        return
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    insights.dependencies_count += len(deps)
    insights.dependency_names.extend(sorted(d.lower() for d in deps))
    for dep, label in _FRAMEWORK_HINTS.items():
        if dep in deps:
            insights.framework = insights.framework or label
    for tf in ("jest", "vitest", "mocha"):
        if tf in deps:
            insights.test_framework = insights.test_framework or tf
    insights.build_system = insights.build_system or "npm"
    insights.language = insights.language or "javascript"


def _parse_requirements(path: Path, insights: ConfigInsights) -> None:
    """requirements.txt 轻量解析：提取依赖名（跳过注释/选项行/-r 引用/本地路径）"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("requirements 读取失败，跳过: %s", exc)
        return
    dep_names = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", ".", "git+", "http")):
            continue
        name = re.split(r"[<>=!~;\[\s]", line, 1)[0].strip().lower()
        if name:
            dep_names.add(name)
    insights.dependencies_count += len(dep_names)
    insights.dependency_names.extend(sorted(dep_names))
    for dep, label in _FRAMEWORK_HINTS.items():
        if dep in dep_names:
            insights.framework = insights.framework or label
    for tf in _TEST_FRAMEWORKS:
        if tf.split()[0] in dep_names:
            insights.test_framework = insights.test_framework or tf
    insights.build_system = insights.build_system or "pip"
    insights.language = insights.language or "python"


def _parse_pom(path: Path, insights: ConfigInsights) -> None:
    """pom.xml 轻量解析：artifactId 正则提取（不引 XML 解析器，容错优先）"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("pom.xml 读取失败，跳过: %s", exc)
        return
    artifacts = {
        m.group(1).strip().lower()
        for m in re.finditer(r"<artifactId>\s*([^<]+?)\s*</artifactId>", text)
    }
    insights.dependencies_count += len(artifacts)
    for hint, label in _POM_FRAMEWORK_HINTS.items():
        if any(hint in a for a in artifacts):
            insights.framework = insights.framework or label
    for hint, label in _POM_TEST_FRAMEWORKS.items():
        if any(a == hint or a.startswith(hint + "-") for a in artifacts):
            insights.test_framework = insights.test_framework or label
    insights.build_system = insights.build_system or "maven"
    insights.language = insights.language or "java"


def scan_configs(project_root: Path, config_files: List[Path], convention_files: List[Path],
                 ci_files: List[Path], code_files: List[Path]) -> ConfigInsights:
    insights = ConfigInsights()
    root = Path(project_root)

    for path in config_files:
        if path.name == "pyproject.toml":
            _parse_pyproject(path, insights)
        elif path.name == "package.json":
            _parse_package_json(path, insights)
        elif path.name == "pom.xml":
            _parse_pom(path, insights)
        elif path.suffix.lower() == ".txt" and path.name.lower().startswith("requirements"):
            _parse_requirements(path, insights)
        elif path.name.lower() == "makefile":
            insights.build_system = insights.build_system or "make"

    # 语言占比：按代码文件扩展名分布（行数统计在 P2.6 代码层，核心层按文件数）
    if code_files:
        ext_count: Dict[str, int] = {}
        ext_lang = {".py": "python", ".js": "javascript", ".ts": "typescript", ".vue": "vue",
                    ".java": "java", ".go": "go", ".rs": "rust", ".cs": "csharp", ".cpp": "cpp"}
        for path in code_files:
            lang = ext_lang.get(path.suffix.lower())
            if lang:
                ext_count[lang] = ext_count.get(lang, 0) + 1
        if ext_count:
            top, count = max(ext_count.items(), key=lambda kv: kv[1])
            if count / sum(ext_count.values()) >= 0.4:  # §3.8：主语言占比 ≥ 40%
                insights.language = top

    insights.dependency_names = list(dict.fromkeys(insights.dependency_names))[:50]
    insights.conventions = sorted({p.name for p in convention_files})
    if ci_files:
        names = {p.name for p in ci_files}
        if any(n.endswith((".yml", ".yaml")) for n in names) or any(".github" in p.parts for p in ci_files):
            insights.ci_pipeline = "github-actions"
        elif any(n.startswith("Jenkinsfile") for n in names):
            insights.ci_pipeline = "jenkins"
        elif ".gitlab-ci.yml" in names:
            insights.ci_pipeline = "gitlab-ci"

    # 无依赖声明时的兜底推断（§3.8：依赖声明优先于目录/配置文件推断）
    if not insights.test_framework and insights.language == "python":
        if (root / "tests").is_dir() or (root / "pytest.ini").is_file() or (root / "tox.ini").is_file():
            insights.test_framework = "pytest"
    return insights
