"""PII / 泄漏扫描门禁：数据集发布前的自动检查。

策略：命中即违规，除非命中串在 pii_allowlist.txt 里（逐行精确匹配，
``#`` 开头为注释）。违规即非零退出——把「不泄漏」从人肉确认变成构建门禁。

扫描类别：
- email          邮箱（公开上游联系人走 allowlist）
- secret         API key / token 形态（sk- / crsr_ / ghp_ / glpat- / AKIA / Bearer / JWT）
- local_path     本机路径（C:\\Users\\、/Users/、/home/<name>/ 等）
- cjk            数据文件中出现中文字符（题目与语料应为英文）
- personal       个人标识（作者名、个人账号、私有仓库地址）

    python -X utf8 benchmark/engmem/pii_scan.py <文件...>
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWLIST_PATH = HERE / "pii_allowlist.txt"

# 个人标识：作者自己选择只出现在 dataset card（README.md），不得出现在数据文件里。
# 往数据文件里扫到任何一条都算违规，不走 allowlist。
PERSONAL_MARKERS = [
    "文星",
    "wenxing",
    "many_money",
    "apollo-lwx",
    "gitee.com",
]

PATTERNS: dict[str, re.Pattern[str]] = {
    # (?<!\\) 防止 JSON 转义 \n 的 n 粘到后文误判；邮箱本体逐行走 allowlist 人工确认
    "email": re.compile(r"(?<!\\)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "secret": re.compile(
        r"(?:(?<![A-Za-z])sk-[A-Za-z0-9_-]{8,}"  # 不匹配 flask-/task- 内的子串
        r"|crsr_[0-9a-f]{16,}"
        r"|ghp_[A-Za-z0-9]{20,}"
        r"|glpat-[A-Za-z0-9_-]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|Bearer\s+[A-Za-z0-9._-]{20,}"
        r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})"
    ),
    "local_path": re.compile(
        r"(?:[A-Za-z]:\\(?:Users|project|study|work)\\|/Users/[^/\s]+|/home/[^/\s]+/)"
    ),
    "cjk": re.compile(r"[一-鿿]"),
}


@dataclass
class Finding:
    source: str
    line_no: int
    kind: str
    match: str

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.source}:{self.line_no} [{self.kind}] {self.match}"


def load_allowlist(path: Path = ALLOWLIST_PATH) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def scan_text(
    text: str, source: str = "<text>", allowlist: set[str] | None = None
) -> list[Finding]:
    allow = allowlist if allowlist is not None else load_allowlist()
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                token = m.group(0)
                if token in allow or token.lower() in allow:
                    continue
                findings.append(Finding(source, line_no, kind, token))
        lowered = line.lower()
        for marker in PERSONAL_MARKERS:
            if marker.lower() in lowered:
                findings.append(Finding(source, line_no, "personal", marker))
    return findings


def scan_file(path: Path, allowlist: set[str] | None = None) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_text(text, source=str(path), allowlist=allowlist)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    allow = load_allowlist()
    all_findings: list[Finding] = []
    for arg in args:
        p = Path(arg)
        if not p.is_file():
            print(f"!! 文件不存在: {p}")
            return 2
        all_findings.extend(scan_file(p, allowlist=allow))
    if all_findings:
        print(f"!! PII 门禁未通过：{len(all_findings)} 处命中")
        for f in all_findings[:50]:
            print(f"  - {f}")
        if len(all_findings) > 50:
            print(f"  ... 其余 {len(all_findings) - 50} 处省略")
        print("若是公开上游联系邮箱等误报，加入 benchmark/engmem/pii_allowlist.txt")
        return 1
    print(f"PII 门禁通过：{len(args)} 个文件无违规命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
