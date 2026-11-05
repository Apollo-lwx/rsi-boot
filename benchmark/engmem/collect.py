"""EngMem-Bench 采集器：浅克隆清单仓库，抽取工程规则候选段落。

    python -X utf8 benchmark/engmem/collect.py              # 全量
    python -X utf8 benchmark/engmem/collect.py requests flask   # 只跑指定仓库
    python -X utf8 benchmark/engmem/collect.py --skip-clone     # 只重新抽取

产物：
- benchmark/.data/repos/<name>/            浅克隆（--depth 1，.git 保留以取 commit sha）
- benchmark/engmem/raw/<name>.yaml         候选段落（文件+行号+正文，按类型粗分）
- benchmark/engmem/repos.lock.yaml         克隆时的 commit 钉版

抽取分三类候选（后续人工/模型改写成题目）：
- convention：含 must/should/always/please/ensure/make sure/we use 等约定语气
- prohibition：never/do not/don't/must not/avoid/not allowed 等禁止语气
- fix：CHANGELOG/HISTORY/NEWS 里的 fixed/bugfix/regression 条目
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REPOS_DIR = ROOT / "benchmark" / ".data" / "repos"
RAW_DIR = HERE / "raw"
LOCK = HERE / "repos.lock.yaml"

# 候选来源文件（相对仓库根），按优先级找，找到即收
DOC_PATTERNS = [
    "AGENTS.md", "CLAUDE.md", "AI_POLICY.md", ".github/AI_POLICY.md",
    "SECURITY.md", ".github/SECURITY.md",
    "CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt", "CONTRIBUTING",
    ".github/CONTRIBUTING.md", ".github/CONTRIBUTING.rst",
    "docs/contributing.md", "docs/contributing.rst",
    "docs/en/docs/contributing.md",
    "docs/development.md", "docs/development.rst",
    "README.md", "README.rst",
]
CHANGELOG_PATTERNS = [
    "CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt", "CHANGELOG",
    "HISTORY.md", "HISTORY.rst", "HISTORY.txt",
    "NEWS.md", "NEWS.rst", "NEWS.txt", "NEWS",
    "CHANGES.md", "CHANGES.rst", "CHANGES.txt", "CHANGES",
    "docs/release-notes.md", "docs/en/docs/release-notes.md",
    "docs/release-notes.rst", "docs/releases.md", "docs/whatsnew.rst",
    "doc/en/changelog.rst", "docs/news.rst", "docs/releases/v*.rst",
    "docs/releases.rst",
]
MAX_FILE_CHARS = 60000      # 单文件抽取上限（README 类长文截断）
MAX_SECTION_CHARS = 4000    # 单节候选上限

_CONVENTION_RE = re.compile(
    r"\b(must|should|shall|always|required|please|make sure|ensure|"
    r"we use|convention|style|prefer|recommended)\b", re.IGNORECASE)
_PROHIBITION_RE = re.compile(
    r"\b(never|do not|don'?t|must not|shall not|avoid|not allowed|"
    r"forbidden|no longer support|unsupported)\b", re.IGNORECASE)
_FIX_RE = re.compile(
    r"\b(fixed|fixes|bugfix|regression|hotfix|patched|CVE-\d+|resolved)\b",
    re.IGNORECASE)

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")          # markdown
_RST_UNDER_RE = re.compile(r"^([=\-~^\"'`]{3,})\s*$")    # rst 下划线式标题
_RST_LEVEL = {"=": 1, "-": 2, "~": 3, "^": 4}


def _load_manifest() -> dict:
    return yaml.safe_load((HERE / "repos.yaml").read_text(encoding="utf-8"))


def _clone(name: str, url: str) -> str:
    dest = REPOS_DIR / name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
            check=True, timeout=280,
        )
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return sha


def _sections(path: Path) -> list[tuple[str, int, str]]:
    """把文档切成节：返回 (标题, 起始行, 正文)。markdown 与 rst 都按标题切。
    围栏代码块（``` 或 ~~~）内部不做标题检测，避免把代码 fence 当成 rst 下划线。
    正文首行附上级标题面包屑（如 "0.27.2 (date) > Fixed"），让「Fixed」这类
    通用小节在检索时能匹配到所属版本号。"""
    text = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
    lines = text.splitlines()
    heads: list[tuple[int, str, int]] = []  # (行号, 标题, 级别)
    # 只有 markdown 才可能有围栏代码块；rst 里 ~~~ 是标题下划线，不能当 fence
    track_fence = path.suffix.lower() in (".md", ".markdown", ".mdx", "")
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if track_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            heads.append((i, m.group(2).strip(), len(m.group(1))))
        elif i + 1 < len(lines) and line.strip() and _RST_UNDER_RE.match(lines[i + 1]):
            ch = lines[i + 1].strip()[0]
            heads.append((i, line.strip(), _RST_LEVEL.get(ch, 5)))
    if not heads:
        return [("(whole)", 1, text[:MAX_SECTION_CHARS])]
    out = []
    preamble = "\n".join(lines[:heads[0][0]]).strip()
    if preamble:
        out.append(("(preamble)", 1, preamble[:MAX_SECTION_CHARS]))
    for idx, (line_no, title, level) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = "\n".join(lines[line_no:end]).strip()
        if not body:
            continue
        crumb: list[str] = []
        min_level = level
        for _ln, t, lv in reversed(heads[:idx]):
            if lv < min_level:
                crumb.append(t)
                min_level = lv
        crumb.reverse()
        if crumb:
            body = f"{' > '.join(crumb)}\n{body}"
        out.append((title, line_no + 1, body[:MAX_SECTION_CHARS]))
    return out


def _changelog_files(repo_dir: Path) -> list[Path]:
    """取第一个命中的 changelog 来源；支持 glob（如 tornado 的 docs/releases/v*.rst，
    最多取 5 个最新版本文件）。过短的指针文件（如 uvicorn/psutil）跳过。"""
    for rel in CHANGELOG_PATTERNS:
        if "*" in rel:
            matches = sorted(repo_dir.glob(rel), reverse=True)[:5]
            matches = [m for m in matches if m.stat().st_size >= 500]
            if matches:
                return matches
            continue
        path = repo_dir / rel
        if path.is_file() and path.stat().st_size >= 500:
            return [path]
    return []


def _collect_repo(name: str) -> dict:
    repo_dir = REPOS_DIR / name
    conventions: list[dict] = []
    prohibitions: list[dict] = []
    fixes: list[dict] = []
    seen_files: set[str] = set()
    for rel in DOC_PATTERNS:
        path = repo_dir / rel
        if not path.is_file():
            continue
        seen_files.add(rel)
        for title, line_no, body in _sections(path):
            entry = {"file": rel, "line": line_no, "section": title, "text": body}
            if _PROHIBITION_RE.search(body):
                prohibitions.append(entry)
            elif _CONVENTION_RE.search(body):
                conventions.append(entry)
    for path in _changelog_files(repo_dir):
        rel = str(path.relative_to(repo_dir)).replace("\\", "/")
        seen_files.add(rel)
        for title, line_no, body in _sections(path):
            if _FIX_RE.search(body):
                fixes.append({"file": rel, "line": line_no,
                              "section": title, "text": body})
    return {
        "convention_candidates": conventions,
        "prohibition_candidates": prohibitions,
        "fix_candidates": fixes,
        "files_seen": sorted(seen_files),
    }


def main() -> None:
    args = sys.argv[1:]
    skip_clone = "--skip-clone" in args
    only = [a for a in args if not a.startswith("--")]
    manifest = _load_manifest()["repos"]
    lock: dict = {}
    if LOCK.exists():
        lock = yaml.safe_load(LOCK.read_text(encoding="utf-8")) or {}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for repo in manifest:
        name = repo["name"]
        if only and name not in only:
            continue
        if not skip_clone:
            print(f"clone {name} ...", flush=True)
            sha = _clone(name, repo["url"])
            lock[name] = {"url": repo["url"], "commit": sha,
                          "license": repo["license"]}
            LOCK.write_text(yaml.safe_dump(lock, allow_unicode=True),
                            encoding="utf-8")
        print(f"extract {name} ...", flush=True)
        collected = _collect_repo(name)
        (RAW_DIR / f"{name}.yaml").write_text(
            yaml.safe_dump(collected, allow_unicode=True, width=120),
            encoding="utf-8",
        )
        print(
            f"{name}: conv={len(collected['convention_candidates'])} "
            f"prohib={len(collected['prohibition_candidates'])} "
            f"fix={len(collected['fix_candidates'])}",
            flush=True,
        )


if __name__ == "__main__":
    main()
