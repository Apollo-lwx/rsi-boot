"""EngMem-Bench 构建器：把人工编写的题目解析到语料 doc id，产出 bench.yaml。

语料 = 每个仓库的工程文档（AGENTS/CONTRIBUTING/README/docs/contributing 等）
按节切开，一节一条记忆：id = <repo>:<file>#<起始行>。

题目在 benchmark/engmem/authored/<repo>.yaml，gold 写成 "<file>::<节标题>"，
构建时解析到语料 id（解析失败即报错，宁可构建挂也不留悬空 gold）。

    python -X utf8 benchmark/engmem/build.py            # 全量构建 + 统计
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REPOS_DIR = ROOT / "benchmark" / ".data" / "repos"
AUTHORED = HERE / "authored"
OUT = HERE / "bench.yaml"

sys.path.insert(0, str(HERE))
from collect import (  # noqa: E402
    CHANGELOG_PATTERNS,
    DOC_PATTERNS,
    _changelog_files,
    _load_manifest,
    _sections,
)


def _corpus(repo: str) -> list[dict]:
    repo_dir = REPOS_DIR / repo
    docs: list[dict] = []
    sources = list(DOC_PATTERNS)
    sources.extend(
        str(p.relative_to(repo_dir)).replace("\\", "/")
        for p in _changelog_files(repo_dir)
    )
    for rel in sources:
        path = repo_dir / rel
        if not path.is_file():
            continue
        for title, line_no, body in _sections(path):
            docs.append({
                "id": f"{repo}:{rel}#{line_no}",
                "title": f"{repo} {rel} — {title}",
                "file": rel,
                "line": line_no,
                "section": title,
                "content": body,
            })
    return docs


def main() -> None:
    manifest = _load_manifest()["repos"]
    lock = {}
    lock_path = HERE / "repos.lock.yaml"
    if lock_path.exists():
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
    bench = {"version": "0.1", "repos": []}
    stats = {"convention": 0, "prohibition": 0, "fix": 0}
    problems: list[str] = []
    for repo in manifest:
        name = repo["name"]
        authored_path = AUTHORED / f"{name}.yaml"
        if not authored_path.exists():
            continue
        authored = yaml.safe_load(authored_path.read_text(encoding="utf-8"))
        corpus = _corpus(name)
        by_anchor: dict[str, list[str]] = {}
        for doc in corpus:
            by_anchor.setdefault(f"{doc['file']}::{doc['section']}", []).append(doc["id"])
        questions: list[dict] = []
        for q in authored.get("questions") or []:
            gold_ids: list[str] = []
            for anchor in q.get("gold") or []:
                # 锚点支持 "file::section@N"：同名小节取第 N 个（1 起），默认第一个
                key, _, occ = anchor.rpartition("@")
                if not key or not occ.isdigit():
                    key, occ_n = anchor, 1
                else:
                    occ_n = int(occ)
                ids = by_anchor.get(key) or []
                doc_id = ids[occ_n - 1] if 0 < occ_n <= len(ids) else None
                if doc_id is None:
                    problems.append(f"{name}/{q.get('id')}: 解析不到 gold '{anchor}'")
                    continue
                gold_ids.append(doc_id)
            if not gold_ids:
                problems.append(f"{name}/{q.get('id')}: 没有可用 gold，跳过")
                continue
            questions.append({
                "id": f"{name}-{q['id']}",
                "type": q["type"],
                "difficulty": q.get("difficulty", "medium"),
                "question": q["question"],
                "answer": q.get("answer", ""),
                "gold": gold_ids,
            })
            stats[q["type"]] = stats.get(q["type"], 0) + 1
        bench["repos"].append({
            "name": name,
            "url": repo["url"],
            "license": repo["license"],
            "commit": (lock.get(name) or {}).get("commit"),
            "corpus": corpus,
            "questions": questions,
        })
        print(f"{name}: corpus={len(corpus)} questions={len(questions)}", flush=True)
    OUT.write_text(yaml.safe_dump(bench, allow_unicode=True, width=140),
                   encoding="utf-8")
    total = sum(stats.values())
    print(f"bench.yaml: repos={len(bench['repos'])} questions={total} {stats}",
          flush=True)
    if problems:
        print("!! 解析问题：", flush=True)
        for line in problems:
            print(f"  - {line}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
