"""EngMem-Bench → HuggingFace datasets 打包器。

把 bench.yaml 转成 HF 可直接上传的纯 JSONL 包：

    dist/engmem-bench/
    ├── README.md          dataset card（YAML frontmatter + 说明 + 引用）
    ├── questions.jsonl    315 题：id/repo/type/difficulty/question/answer/gold
    ├── corpus.jsonl       语料：id/repo/file/section/line/title/content
    ├── repos.json         21 个仓库元数据（url/license/钉死 commit）
    └── load_example.py    零依赖加载示例

打包前过 PII 门禁（pii_scan）：违规即失败，不出半份包。
dist/ 在 .gitignore 里，产物只用于上传，不入库。

    python -X utf8 benchmark/engmem/package_hf.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench.yaml"
OUT = HERE / "dist" / "engmem-bench"

sys.path.insert(0, str(HERE))
from pii_scan import load_allowlist, scan_text  # noqa: E402

CARD_TEMPLATE = """---
license: cc-by-4.0
pretty_name: EngMem-Bench
language:
  - en
task_categories:
  - text-retrieval
  - question-answering
tags:
  - agent-memory
  - coding-agents
  - retrieval
  - benchmark
  - software-engineering
size_categories:
  - n<1K
configs:
  - config_name: questions
    data_files: questions.jsonl
  - config_name: corpus
    data_files: corpus.jsonl
---

# EngMem-Bench

A retrieval benchmark for **engineering memory** of coding agents.

Existing agent-memory benchmarks (LoCoMo, LongMemEval) measure *conversational*
recall. EngMem-Bench measures a different object: the durable engineering
knowledge a coding agent must retrieve from a repository — project
**conventions**, explicit **prohibitions**, and historical **fix** knowledge.

- **{n_questions} human-authored questions** over **{n_repos} real open-source
  Python repositories** (pinned commits, MIT/Apache/BSD-family licenses).
- Every answer is anchored to a verifiable `file::section` in the corpus;
  anchors are resolved at build time, so label drift fails the build instead
  of silently corrupting labels.
- Three question types: `convention` ({n_conv}), `prohibition` ({n_pro}),
  `fix` ({n_fix}). Difficulty: easy {n_easy} / medium {n_med} / hard {n_hard}.
- Annotation quality audited by a three-LLM verification panel
  (Fleiss' κ = 0.738 on an 87-question stratified sample; five real
  anchor/label defects found and fixed under author adjudication).

## Data files

| file | records | fields |
| --- | --- | --- |
| `questions.jsonl` | {n_questions} | `id`, `repo`, `type`, `difficulty`, `question`, `answer`, `gold` (list of corpus ids) |
| `corpus.jsonl` | {n_corpus} | `id`, `repo`, `file`, `section`, `line`, `title`, `content` |
| `repos.json` | {n_repos} | `name`, `url`, `license`, `commit` |

The corpus is sliced from real agent-visible documentation in each repository
(`AGENTS.md` / `CONTRIBUTING` / `SECURITY` / `README` / `docs/**` / changelogs),
one document section per record. Full repositories are **not** redistributed;
the build scripts in the source repository clone them at the pinned commits.

## Usage

```python
import json

questions = [json.loads(l) for l in open("questions.jsonl", encoding="utf-8")]
corpus = {{d["id"]: d for d in
          map(json.loads, open("corpus.jsonl", encoding="utf-8"))}}

q = questions[0]
print(q["question"])
print("gold sections:", [corpus[g]["title"] for g in q["gold"]])
```

`load_example.py` in this dataset ships the same snippet with a tiny
Hit@k evaluation skeleton.

Metrics used by the source paper: Hit@k / Recall@k / MRR over retrieval hits
(not generation), grouped by question type and difficulty.

## Licensing

- **Questions** (`questions.jsonl`): CC-BY-4.0, © Wenxing (Apollo-lwx).
- **Corpus excerpts** (`corpus.jsonl`): retain the upstream license of each
  source repository (MIT / Apache-2.0 / BSD-3-Clause / MIT-CMU; see
  `repos.json`), all of which permit redistribution of excerpts.

## Privacy

The dataset contains only public documentation of public open-source
repositories and questions authored against them. It contains no personal
or enterprise data of the authors. An automated PII gate (secret shapes,
local paths, personal markers, e-mail allowlist review) runs at package
build time; the build fails on any non-allowlisted hit.

## Source & citation

Code, harnesses, and results: https://github.com/Apollo-lwx/rsi-boot
(`benchmark/engmem/`).

```bibtex
@misc{{engmem-bench-2026,
  title  = {{EngMem-Bench: A Retrieval Benchmark for Engineering Memory of Coding Agents}},
  author = {{Wenxing (Apollo-lwx)}},
  year   = {{2026}},
  url    = {{https://github.com/Apollo-lwx/rsi-boot}},
  note   = {{Companion artifact of "Governed Engineering Memory for Coding Agents"
             (under submission). ORCID: 0009-0003-3626-6660}}
}}
```

## Known limitations

- Single human annotator (AI-pre-annotated, human-verified anchors); no
  double-annotation agreement — disclosed in the source paper.
- Questions and corpus are in English (the source documentation is English).
- The benchmark evaluates retrieval hits, not end-task answer generation.
"""

LOAD_EXAMPLE = '''"""EngMem-Bench 零依赖加载示例（标准库即可）。"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

questions = [
    json.loads(line)
    for line in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
corpus = {
    doc["id"]: doc
    for doc in map(
        json.loads,
        (HERE / "corpus.jsonl").read_text(encoding="utf-8").splitlines(),
    )
}

q = questions[0]
print("question:", q["question"])
print("gold sections:", [corpus[g]["title"] for g in q["gold"]])


def hit_at_k(ranked_ids: list[str], gold_ids: list[str], k: int = 5) -> float:
    """最小评测骨架：ranked_ids 是你的检索器输出的 doc id 排序。"""
    return float(any(g in ranked_ids[:k] for g in gold_ids))
'''


def _load_bench() -> dict:
    if not BENCH.exists():
        sys.exit(f"bench.yaml 不存在，先跑: python -X utf8 benchmark/engmem/build.py")
    return yaml.safe_load(BENCH.read_text(encoding="utf-8"))


def main() -> None:
    bench = _load_bench()
    repos = bench["repos"]

    questions: list[dict] = []
    corpus: list[dict] = []
    repos_meta: list[dict] = []
    stats = {"convention": 0, "prohibition": 0, "fix": 0,
             "easy": 0, "medium": 0, "hard": 0}
    for r in repos:
        repos_meta.append({
            "name": r["name"],
            "url": r["url"],
            "license": r["license"],
            "commit": r.get("commit"),
        })
        for doc in r["corpus"]:
            corpus.append({"repo": r["name"], **doc})
        for q in r["questions"]:
            questions.append({"repo": r["name"], **q})
            stats[q["type"]] += 1
            stats[q.get("difficulty", "medium")] += 1

    payload = {
        "questions.jsonl": "".join(
            json.dumps(q, ensure_ascii=False) + "\n" for q in questions
        ),
        "corpus.jsonl": "".join(
            json.dumps(d, ensure_ascii=False) + "\n" for d in corpus
        ),
        "repos.json": json.dumps(repos_meta, ensure_ascii=False, indent=2) + "\n",
    }

    # —— PII 门禁：不过门禁不出包 ——
    allow = load_allowlist()
    findings = []
    for name, text in payload.items():
        findings.extend(scan_text(text, source=name, allowlist=allow))
    if findings:
        print(f"!! PII 门禁未通过：{len(findings)} 处命中，未产出任何文件")
        for f in findings[:50]:
            print(f"  - {f}")
        print("误报请确认来源公开后加入 benchmark/engmem/pii_allowlist.txt")
        sys.exit(1)

    card = CARD_TEMPLATE.format(
        n_questions=len(questions),
        n_corpus=len(corpus),
        n_repos=len(repos_meta),
        n_conv=stats["convention"],
        n_pro=stats["prohibition"],
        n_fix=stats["fix"],
        n_easy=stats["easy"],
        n_med=stats["medium"],
        n_hard=stats["hard"],
    )

    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in payload.items():
        (OUT / name).write_text(text, encoding="utf-8")
    (OUT / "README.md").write_text(card, encoding="utf-8")
    (OUT / "load_example.py").write_text(LOAD_EXAMPLE, encoding="utf-8")

    print(f"PII 门禁通过。产物 -> {OUT}")
    print(f"  questions.jsonl: {len(questions)} 题 "
          f"(conv {stats['convention']} / pro {stats['prohibition']} / fix {stats['fix']};"
          f" easy {stats['easy']} / med {stats['medium']} / hard {stats['hard']})")
    print(f"  corpus.jsonl: {len(corpus)} 节语料, repos.json: {len(repos_meta)} 仓库")


if __name__ == "__main__":
    main()
