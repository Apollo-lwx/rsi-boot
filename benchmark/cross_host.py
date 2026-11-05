"""跨宿主一致性评测：同一份 .rsi 被不同宿主读取，召回是否一致。

协议：先在临时项目里造一份带种子记忆的 .rsi（--seed 只需一次），然后每个
宿主各跑一遍读取端：

    python -X utf8 benchmark/cross_host.py --seed                 # 造语料（一次）
    python -X utf8 benchmark/cross_host.py --host=rsi-a           # RSI 自己读（基座）
    python -X utf8 benchmark/cross_host.py --host=rsi-b           # RSI 二次打开（确定性）
    # 以下需要对应宿主环境，由人工在各自宿主里执行：
    #   cursor: 让 Cursor 的 agent 对每个查询调 rsi_recall，把返回 id 顺序存成
    #           benchmark/results/cross-host/host-cursor.jsonl（每行 {"qid","ids"}）
    #   claude: 同上，host-claude.jsonl
    python -X utf8 benchmark/cross_host.py --report               # 汇总一致性表

一致性指标：对同一查询，两宿主 top-5 的 Jaccard 与「首位是否相同」。RSI 两次
自读应完全一致（Jaccard=1.0），这是宿主间比较可信的基座。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "benchmark" / "results" / "cross-host"
SEED_DIR = ROOT / "benchmark" / ".data" / "cross-host-seed"

# 固定种子语料与查询：任何宿主都能重放到同一状态
SEED_DOCS = [
    ("doc-deploy", "Deployment freeze policy",
     "Deployments are frozen during the last week of each quarter. "
     "No production releases between Dec 25 and Jan 2. Hotfixes need "
     "sign-off from two on-call leads."),
    ("doc-style", "Python style rules",
     "This project uses black with line length 100. Type annotations are "
     "required on all public functions. Do not introduce new dependencies "
     "without an ADR."),
    ("doc-incident", "Incident 2026-08 cache stampede",
     "Root cause: missing negative caching on the pricing API. Fixed by "
     "adding a 30s negative cache and request coalescing. Regression test "
     "lives in tests/test_pricing_cache.py."),
    ("doc-secret", "Secrets handling",
     "Never commit .env files. Secrets are injected via the vault sidecar. "
     "Rotating a key requires updating the vault manifest first, then the "
     "deployment chart."),
    ("doc-review", "Code review rules",
     "Every PR needs one approval from a module owner. Generated files "
     "(pb.go, openapi client) must not be hand-edited. CI must be green "
     "before merge, no exceptions."),
]

QUERIES = [
    ("q1", "When are production deployments frozen?"),
    ("q2", "What is the black line length for this project?"),
    ("q3", "What caused the cache stampede incident and how was it fixed?"),
    ("q4", "How do I rotate an API key?"),
    ("q5", "Can I hand-edit generated protobuf files?"),
]


def _seed() -> None:
    if SEED_DIR.exists():
        shutil.rmtree(SEED_DIR)
    SEED_DIR.mkdir(parents=True)
    from benchmark.recall_board import _write_docs

    rows = [(key, title, content) for key, title, content in SEED_DOCS]
    _write_docs(SEED_DIR / ".rsi", rows)
    (SEED_DIR / "queries.json").write_text(
        json.dumps(QUERIES, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"seeded -> {SEED_DIR}", flush=True)


def _run_host(name: str) -> None:
    if not (SEED_DIR / ".rsi").exists():
        print("seed missing; run --seed first", flush=True)
        sys.exit(2)
    from rsi_boot.bootstrap import build_runtime

    OUT.mkdir(parents=True, exist_ok=True)

    async def _go() -> list[dict]:
        runtime = await build_runtime(project_root=SEED_DIR)
        try:
            out = []
            for qid, query in QUERIES:
                payload = await runtime.recall.recall(
                    query, runtime.project_id, user_id="cross-host", top_k=5,
                )
                ids = [item["id"] for item in payload.get("items") or []]
                out.append({"qid": qid, "ids": ids})
            return out
        finally:
            await runtime.close()

    rows = asyncio.run(_go())
    path = OUT / f"host-{name}.jsonl"
    with path.open("w", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{name}: {len(rows)} queries -> {path}", flush=True)


def _load_host(path: Path) -> dict[str, list[str]]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["qid"]] = row["ids"]
    return out


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 1.0


def _report() -> None:
    hosts = {p.stem.removeprefix("host-"): _load_host(p)
             for p in sorted(OUT.glob("host-*.jsonl"))}
    if len(hosts) < 2:
        print(f"need >=2 host files in {OUT}, got {list(hosts)}", flush=True)
        sys.exit(2)
    names = list(hosts)
    lines = [
        "# 跨宿主一致性",
        "",
        "同一份 .rsi，各宿主对同一组查询的 top-5 召回比较。",
        "RSI 两次自读应完全一致（Jaccard=1.0），是宿主间比较可信的基座。",
        "",
        "| 宿主对 | 平均 Jaccard@5 | 首位一致率 |",
        "|---|---:|---:|",
    ]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = hosts[names[i]], hosts[names[j]]
            common = sorted(set(a) & set(b))
            if not common:
                continue
            jac = sum(_jaccard(a[q], b[q]) for q in common) / len(common)
            first = sum(
                1 for q in common
                if (a[q][:1] or [None]) == (b[q][:1] or [None])
            ) / len(common)
            lines.append(f"| {names[i]} vs {names[j]} | {jac:.3f} | {first * 100:.1f}% |")
    lines.append("")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)


def main() -> None:
    args = sys.argv[1:]
    if "--seed" in args:
        _seed()
        return
    if "--report" in args:
        _report()
        return
    for arg in args:
        if arg.startswith("--host="):
            _run_host(arg.split("=", 1)[1])
            return
    print(__doc__, flush=True)


if __name__ == "__main__":
    main()
