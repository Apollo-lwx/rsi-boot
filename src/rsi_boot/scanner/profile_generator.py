"""初始画像生成（§10.9.4 + §3.8 推理规则）：综合多源信号写入 UserProfile。

信号来源：配置解析（语言/框架/测试框架/构建/CI）、文档与测试文件统计
（doc_quality/test_culture）、Git 历史（commit 风格/贡献领域，P2.6）、
代码 import 统计（≥10 → 技术标签，P2.6）。所有推断遵循「低于阈值宁可留空」原则。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Counter, List, Optional

from ..core.models import UserProfile
from ..data.sqlite import SQLiteClient
from .config_scanner import ConfigInsights
from .git_analyzer import GitInsights

logger = logging.getLogger(__name__)

_README_REQUIRED_SECTIONS = ("安装", "使用", "配置", "install", "usage", "config")


def assess_doc_quality(doc_files: List[Path], project_root: Path) -> str:
    """README 必备章节齐备度 + docs/ 文档数（§3.8：齐备度 ≥80% 且 docs ≥3 篇 = good）"""
    from .validator import read_text_tolerant

    readme = next((p for p in doc_files if p.stem.lower() == "readme"), None)
    docs_count = sum(1 for p in doc_files if "docs" in {part.lower() for part in p.parts})
    if not readme:
        return "weak"
    text = (read_text_tolerant(readme) or "").lower()
    hits = sum(1 for s in _README_REQUIRED_SECTIONS if s in text)
    coverage = hits / 3  # 安装/使用/配置 三类（中英文同义项合并计数去重）
    coverage = min(1.0, coverage)
    if coverage >= 0.8 and docs_count >= 3:
        return "good"
    if coverage >= 0.5 or docs_count >= 1:
        return "moderate"
    return "weak"


def assess_test_culture(test_files: List[Path], code_files: List[Path]) -> str:
    """测试文件数 / 源文件数（§3.8：≥30% strong；10%~30% moderate；<10% weak）"""
    if not code_files:
        return "weak"
    ratio = len(test_files) / len(code_files)
    if ratio >= 0.3:
        return "strong"
    if ratio >= 0.1:
        return "moderate"
    return "weak"


def build_profile(
    user_id: str,
    project_id: str,
    insights: ConfigInsights,
    doc_quality: str,
    test_culture: str,
    git: Optional[GitInsights] = None,
    import_counter: Optional[Counter] = None,
) -> UserProfile:
    profile = UserProfile(user_id=user_id, project_id=project_id)
    if insights.language:
        profile.preferences.role_specific["language"] = insights.language
    if insights.framework:
        profile.preferences.role_specific["framework"] = insights.framework
    if insights.test_framework:
        profile.preferences.role_specific["test_framework"] = insights.test_framework
    profile.project_insights = {
        "architecture": insights.framework or "",
        "build_system": insights.build_system or "",
        "ci_pipeline": insights.ci_pipeline or "",
        "doc_quality": doc_quality,
        "test_culture": test_culture,
    }

    # ---- P2.6 深度信号：Git + import 统计（§3.8 推理规则，低于阈值留空） ----
    expertise: List[str] = []
    # 排除完整 Python 标准库（sys.stdlib_module_names）+ JVM 包名/vendor 前缀噪声
    #（Java 侧已在 code_scanner._java_import_root 归一化，此处为兜底）
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", ())) | {
        "com", "org", "net", "io", "java", "javax", "jakarta", "jdk", "sun",
    }
    if import_counter:
        from .code_scanner import IMPORT_TAG_THRESHOLD

        # import 计数 ≥ 10 的框架/库 → 技术标签
        expertise.extend(
            name for name, count in import_counter.most_common(30)
            if count >= IMPORT_TAG_THRESHOLD and name not in stdlib and not name.startswith("_")
        )
    # 清单依赖名补充种子：小项目 import 计数达不到阈值时画像不致空白（排 import 强信号之后）
    expertise.extend(
        name for name in insights.dependency_names
        if name not in stdlib and not name.startswith("_") and name not in expertise
    )
    if git is not None:
        if git.commit_style:
            profile.preferences.role_specific["commit_style"] = git.commit_style
        # 文件归属 ≥20% 的模块 → 贡献领域（git log 作者名与 user_id 匹配）
        expertise.extend(git.owned_modules(user_id))
        profile.project_insights["git_commits"] = git.total_commits
        profile.project_insights["git_contributors"] = len(git.authors)
    profile.expertise = list(dict.fromkeys(expertise))[:20]  # 单用户标签上限 20 个（§3.8）
    return profile


async def upsert_profile(db: SQLiteClient, profile: UserProfile) -> None:
    """写穿落库（§3.8：主键 (user_id, project_id)；进程内缓存在 P2.4 接入）"""
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (user_id, project_id) DO UPDATE SET profile_data = excluded.profile_data, updated_at = excluded.updated_at
        """,
        (profile.user_id, profile.project_id or "", profile.model_dump_json(), now, now),
    )
    await conn.commit()
