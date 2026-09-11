"""代码骨架扫描（§10.9.2/§10.9.6，P2.6）：AST 静态分析提取代码骨架。

仅提取骨架签名（模块名、类名、函数签名、docstring 摘要、import 统计），
不导入完整源码——避免知识库膨胀与许可证风险（§10.9.6 设计原则）。
Python 走 ast 精确解析；其他语言走正则轻量提取（尽力而为，失败跳过该文件）。
AST 解析失败（语法错误文件）→ 跳过并记录路径（§10.9.9）。
"""

from __future__ import annotations

import ast
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .validator import read_text_tolerant

logger = logging.getLogger(__name__)

# import 计数 ≥ 10 的框架/库 → 技术标签（§3.8 画像推理规则）
IMPORT_TAG_THRESHOLD = 10
_MAX_DOCSTRING_CHARS = 120
_MAX_MEMBERS = 30  # 单模块类/函数签名上限，防巨型文件膨胀

_GENERIC_PATTERNS = {
    ".js": [r"class\s+(\w+)", r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()"],
    ".ts": [r"class\s+(\w+)", r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()"],
    ".vue": [r"class\s+(\w+)", r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()"],
    ".java": [r"(?:class|interface|enum)\s+(\w+)", r"(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\("],
    ".go": [r"type\s+(\w+)\s+(?:struct|interface)", r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\("],
    ".rs": [r"(?:struct|enum|trait|impl)\s+(\w+)", r"fn\s+(\w+)"],
    ".cs": [r"(?:class|interface|struct|enum)\s+(\w+)", r"(?:public|private|protected|internal)?\s*(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\("],
    ".cpp": [r"(?:class|struct)\s+(\w+)", r"[\w:<>\*&]+\s+(\w+)\s*\([^;]*\)\s*\{"],
}

_IMPORT_RE = {
    ".py": re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE),
    # JS/TS/Vue 捕获完整模块说明符（含 @scope/pkg 与路径），由 _js_import_root 归一化
    ".js": re.compile(r"(?:from\s+['\"]([^'\"]+)|require\(['\"]([^'\"]+))", re.MULTILINE),
    ".ts": re.compile(r"from\s+['\"]([^'\"]+)", re.MULTILINE),
    ".vue": re.compile(r"from\s+['\"]([^'\"]+)", re.MULTILINE),
    # Java 取前两段（com.baomidou），由 _java_import_root 归一化出库标签
    ".java": re.compile(r"^\s*import\s+(?:static\s+)?([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)\.", re.MULTILINE),
    ".go": re.compile(r"^\s*\"([a-zA-Z0-9_\-\.]+)\"", re.MULTILINE),
}


def _js_import_root(spec: str) -> Optional[str]:
    """JS/TS 模块说明符归一化：'@scope/pkg/sub' → '@scope/pkg'；'lodash-es' 原样；
    '@/...' 路径别名与 './' 相对路径（自有代码）→ None"""
    spec = spec.strip().strip("\"'").strip()
    if not spec or spec.startswith((".", "/", "@/")):
        return None  # 相对路径、绝对路径、@/ 别名均为自有代码
    if spec.startswith("@"):
        parts = spec.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return "/".join(parts[:2])
        return None  # 残缺说明符
    return spec.split("/")[0]

# Java 包名归一化：JDK 标准库整包跳过；vendor 前缀取第二段作为库标签
_JAVA_STDLIB_PREFIXES = {"java", "javax", "jdk", "sun"}
_JAVA_VENDOR_PREFIXES = {"com", "org", "net", "io", "cn", "edu", "gov", "cc", "jakarta"}


def _java_import_root(package: str) -> Optional[str]:
    """com.baomidou.mybatisplus → baomidou；org.springframework → springframework；
    java.util → None（JDK 噪声）；lombok → lombok"""
    first, _, second = package.partition(".")
    if first in _JAVA_STDLIB_PREFIXES:
        return None
    if first in _JAVA_VENDOR_PREFIXES:
        return second or None
    return first


@dataclass
class ModuleSkeleton:
    """单模块骨架：路径 + 签名摘要 + import 统计"""

    rel_path: str
    language: str
    docstring: str = ""
    symbols: List[str] = field(default_factory=list)  # "class Foo(Bar)" / "def f(a, b)"
    imports: List[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [f"模块 {self.rel_path}（{self.language}）"]
        if self.docstring:
            lines.append(f"说明：{self.docstring}")
        if self.symbols:
            lines.append("结构：")
            lines.extend(f"  - {s}" for s in self.symbols[:_MAX_MEMBERS])
        if self.imports:
            lines.append(f"依赖：{', '.join(self.imports[:15])}")
        return "\n".join(lines)


def _sig_from_node(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = ", ".join(a.arg for a in node.args.args[:8])
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({args})"
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases[:3]) if node.bases else ""
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    return ""


def extract_python_skeleton(source: str, rel_path: str) -> Optional[ModuleSkeleton]:
    """ast 解析 .py：模块 docstring + 顶层类/函数签名 + 类方法 + import 统计"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.info("AST 解析失败，跳过 %s: %s", rel_path, exc)
        return None

    skeleton = ModuleSkeleton(rel_path=rel_path, language="python")
    doc = ast.get_docstring(tree)
    if doc:
        skeleton.docstring = doc[:_MAX_DOCSTRING_CHARS]

    imports: Counter = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports[node.module.split(".")[0]] += 1
    skeleton.imports = [name for name, _ in imports.most_common(20)]

    for node in tree.body:
        sig = _sig_from_node(node)
        if sig:
            skeleton.symbols.append(sig)
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and not member.name.startswith("_"):
                    args = ", ".join(a.arg for a in member.args.args[:8])
                    prefix = "async " if isinstance(member, ast.AsyncFunctionDef) else ""
                    skeleton.symbols.append(f"  {node.name}.{prefix}{member.name}({args})")
    return skeleton


def extract_generic_skeleton(source: str, rel_path: str, ext: str) -> ModuleSkeleton:
    """非 Python 语言：正则提取 class/func 签名（尽力而为）"""
    skeleton = ModuleSkeleton(rel_path=rel_path, language=ext.lstrip("."))
    seen: set[str] = set()
    for pattern in _GENERIC_PATTERNS.get(ext, []):
        for match in re.finditer(pattern, source):
            name = next((g for g in match.groups() if g), None)
            if name and name not in seen and not name.startswith(("_", "if", "for", "while")):
                seen.add(name)
                skeleton.symbols.append(name)
            if len(skeleton.symbols) >= _MAX_MEMBERS:
                break

    import_re = _IMPORT_RE.get(ext)
    if import_re:
        counter: Counter = Counter()
        for match in import_re.finditer(source):
            name = next((g for g in match.groups() if g), "")
            if not name:
                continue
            if ext == ".java":
                name = _java_import_root(name) or ""
            elif ext in (".js", ".ts", ".vue"):
                name = _js_import_root(name) or ""
            else:
                name = name.split(".")[0].split("/")[0]
            if name:
                counter[name] += 1
        skeleton.imports = [name for name, _ in counter.most_common(20)]
    return skeleton


def scan_code(project_root: Path, files: List[Path]) -> List[ModuleSkeleton]:
    """批量提取代码骨架；单文件失败跳过（§10.9.9 部分恢复）"""
    skeletons: List[ModuleSkeleton] = []
    for path in files:
        rel = str(path.relative_to(project_root))
        try:
            source = read_text_tolerant(path)
            if source is None:
                continue
            if path.suffix == ".py":
                skeleton = extract_python_skeleton(source, rel)
            else:
                skeleton = extract_generic_skeleton(source, rel, path.suffix.lower())
            if skeleton and (skeleton.symbols or skeleton.docstring):
                skeletons.append(skeleton)
        except Exception as exc:
            logger.info("代码骨架提取失败，跳过 %s: %s", rel, exc)
    return skeletons


def aggregate_imports(skeletons: List[ModuleSkeleton]) -> Counter:
    """全项目 import 计数（§3.8：≥10 的框架/库 → expertise 技术标签）"""
    counter: Counter = Counter()
    for s in skeletons:
        counter.update(s.imports)
    return counter


def group_by_directory(skeletons: List[ModuleSkeleton]) -> Dict[str, List[ModuleSkeleton]]:
    """按顶层目录聚合（骨架条目过碎时合并写入，减少噪声条目）"""
    groups: Dict[str, List[ModuleSkeleton]] = {}
    for s in skeletons:
        top = s.rel_path.split("\\")[0].split("/")[0] if ("\\" in s.rel_path or "/" in s.rel_path) else "."
        groups.setdefault(top, []).append(s)
    return groups
