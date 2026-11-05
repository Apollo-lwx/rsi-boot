"""纵向自改进实验 v2：治理环在噪声下的传导性与选择性。

    python -X utf8 benchmark/longitudinal.py --out=longitudinal-20261104

三臂（同一事件流、同一探针集 benchmark/probes/longitudinal_v2.yaml，10 轮）：

- governed       治理臂：正确反馈 pending → 人工确认 approve → official；
                 噪声反馈（与种子知识矛盾的错误纠正）被人工**拒绝**——模拟
                 真实 review 的甄别作用。
- approve-all    无门臂：所有反馈（含噪声）一律 approve。测的是「没有人工
                 门时错误反馈会不会污染召回」，噪声暴露率应该 > 0。
- no-promotion   消融臂：反馈只进 pending 永不确认，learn 探针全程 0。

每轮指标：stable 探针通过率、learn 探针通过率、噪声暴露率（stable 查询的
top-5 中出现噪声文档的比例）。合成反馈是受控信号，测治理环的传导与选择，
不是真实用户语料效果——论文中已声明。

输出：out/longitudinal.csv（arm,round,probe,passed,noise_exposed）与 SUMMARY.md。
"""

from __future__ import annotations

import asyncio
import csv
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi_boot.learning.pipeline import extract_from_feedback  # noqa: E402
from rsi_boot.learning.probe_eval import _hit, load_probes  # noqa: E402
from rsi_boot.memory.paths import official_dir  # noqa: E402
from rsi_boot.memory.store import MemoryStore, memory_filename  # noqa: E402
from rsi_boot.memory.types import MemoryDoc  # noqa: E402
from rsi_boot.services.knowledge_service import KnowledgeService  # noqa: E402

PROBES = ROOT / "benchmark" / "probes" / "longitudinal_v2.yaml"
OUT = ROOT / "benchmark" / "results" / "longitudinal-dev"

SEED_DOCS = [
    ("Testing convention",
     "This project uses pytest for all automated testing. Run pytest before "
     "every commit; CI blocks merges on red tests."),
    ("Merge requirements",
     "The main branch is protected. Every pull request needs at least one "
     "approval from a code owner before merging."),
]

# 10 轮注入的正确规则（question 对应探针问题）
ROUND_RULES = [
    ("What must database migrations always support?",
     "All database migrations must be reversible: every migration needs a "
     "tested downgrade path before it can be merged."),
    ("What is the correct command to deploy to staging?",
     "Deploy to staging with `make deploy-staging`. Never run kubectl apply "
     "against staging directly; the make target runs the preflight checks."),
    ("What must never be logged on the payments service?",
     "Never log request bodies on the payments service: PCI scope forbids "
     "persisting payloads. Log only request ids and status codes."),
    ("Which branch do feature branches merge into?",
     "Feature branches merge into develop, never directly into main. Release "
     "branches are cut from develop every second Tuesday."),
    ("What is the required format for commit messages?",
     "Commit messages must follow conventional commits: type(scope): subject, "
     "with the subject in imperative mood, at most 72 characters."),
    ("Who must review changes to the protobuf definitions?",
     "Any change to protobuf definitions requires review by the platform team; "
     "generated stubs must be regenerated in the same commit."),
    ("What timeout should external API calls use?",
     "All external API calls must set an explicit timeout of 30 seconds and "
     "wrap the call in the shared retry helper with exponential backoff."),
    ("Where must feature flags be registered?",
     "Every feature flag must be registered in flags.yaml with an owner and "
     "an expiry date before it can be referenced in code."),
    ("What is the policy on catching broad exceptions?",
     "We never catch bare Exception in library code; catch the specific "
     "exception type and re-raise with context attached."),
    ("Which Python versions must CI test against?",
     "CI must test against Python 3.10 and 3.12 on every pull request; "
     "support for 3.9 was dropped in the 2.0 release."),
]

# 噪声反馈：与种子知识矛盾的错误纠正（第 3/6/9 轮注入）。
# 治理臂应拒绝它们；无门臂会批准，从而污染 stable 查询的 top-5。
# 措辞刻意避开探针 needle（pytest / approval），保证暴露率测量不被
# 噪声文档误命中 needle 污染。
NOISE_RULES = {
    3: ("What test framework does this project use?",
        "Correction: the project has fully migrated to the stdlib unittest "
        "framework; the previous third-party test runner is deprecated and "
        "must not be used for new tests."),
    6: ("What is required before merging a pull request to main?",
        "Correction: sign-off is no longer needed; any maintainer may merge "
        "to main directly without review when CI is green."),
    9: ("What test framework does this project use?",
        "Correction: remaining uses of the legacy third-party test runner "
        "are being removed this sprint; write only stdlib unittest tests "
        "from now on."),
}

STABLE_QUERIES = [
    "What test framework does this project use?",
    "What is required before merging a pull request to main?",
]


def _seed(rsi_dir: Path) -> None:
    store = MemoryStore(rsi_dir)
    for title, content in SEED_DOCS:
        doc = MemoryDoc(id=uuid.uuid4().hex, type="convention",
                        title=title, content=content)
        store.write(doc, dest=official_dir(rsi_dir, "convention")
                    / memory_filename(title, doc.id))


async def _eval_round(rsi_dir: Path, pack: dict,
                      noise_ids: set[str]) -> tuple[dict[str, bool], float]:
    """复制当前 .rsi 到临时目录跑探针；返回 (探针结果, 噪声暴露率)。"""
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.project import derive_project_id

    results: dict[str, bool] = {}
    noise_hits = 0
    with tempfile.TemporaryDirectory(prefix="rsi-long-") as tmp:
        work = Path(tmp)
        shutil.copytree(
            rsi_dir, work / ".rsi",
            ignore=shutil.ignore_patterns("logs", "cache", "work", "__pycache__"),
        )
        runtime = await build_runtime(project_root=work)
        try:
            project_id = derive_project_id(work)
            for probe in pack["probes"]:
                top_k = int(probe.get("top_k") or 5)
                payload = await runtime.recall.recall(
                    str(probe["question"]), project_id, user_id="longitudinal",
                    top_k=top_k,
                )
                needles = probe.get("hit_any") or []
                results[str(probe["id"])] = any(
                    _hit(payload, str(n), top_k=top_k) for n in needles
                )
            for query in STABLE_QUERIES:
                payload = await runtime.recall.recall(
                    query, project_id, user_id="longitudinal", top_k=5,
                )
                ids = {item["id"] for item in payload.get("items") or []}
                ids |= {item["id"] for item in payload.get("prohibitions") or []}
                if ids & noise_ids:
                    noise_hits += 1
        finally:
            await runtime.close()
    exposure = noise_hits / len(STABLE_QUERIES) if noise_ids else 0.0
    return results, exposure


def _inject(rsi_dir: Path, question: str, rule: str) -> set[str]:
    """注入一条 rejected 反馈，返回新增的 pending 文档 id 集合。"""
    store = MemoryStore(rsi_dir)
    before = {d.id for d in store.list_pending(skip_harvest=True)}
    extract_from_feedback(rsi_dir, action="rejected", comment=rule,
                          question=question)
    after = {d.id for d in store.list_pending(skip_harvest=True)}
    return after - before


async def _review(rsi_dir: Path, approve_ids: set[str],
                  reject_ids: set[str]) -> tuple[int, int]:
    """人工确认臂：approve_ids 批准、reject_ids 拒绝（真实 review 路径）。"""
    store = MemoryStore(rsi_dir)
    service = KnowledgeService(store, project_root=rsi_dir.parent)
    for doc_id in approve_ids:
        await service.review(doc_id, project_id="", approve=True)
    for doc_id in reject_ids:
        await service.review(doc_id, project_id="", approve=False)
    return len(approve_ids), len(reject_ids)


async def _run_arm(arm: str, rounds: int, work_root: Path) -> list[dict]:
    pack = load_probes(PROBES)
    repo = work_root / arm
    rsi_dir = repo / ".rsi"
    rsi_dir.mkdir(parents=True, exist_ok=True)
    _seed(rsi_dir)

    rows: list[dict] = []
    noise_ids: set[str] = set()
    base, exposure = await _eval_round(rsi_dir, pack, noise_ids)
    for probe_id, passed in base.items():
        rows.append({"arm": arm, "round": 0, "probe": probe_id,
                     "passed": int(passed), "noise_exposed": f"{exposure:.2f}"})
    print(f"{arm} round=0 pass={sum(base.values())}/{len(base)} "
          f"noise={exposure:.2f}", flush=True)

    for r in range(1, rounds + 1):
        question, rule = ROUND_RULES[(r - 1) % len(ROUND_RULES)]
        good_ids = _inject(rsi_dir, question, rule)
        bad_ids: set[str] = set()
        if r in NOISE_RULES:
            nq, nr = NOISE_RULES[r]
            bad_ids = _inject(rsi_dir, nq, nr)

        if arm == "governed":
            # 人工甄别：正确的批准，噪声的拒绝
            await _review(rsi_dir, good_ids, bad_ids)
        elif arm == "approve-all":
            noise_ids |= bad_ids  # 噪声进入 official，可被召回
            await _review(rsi_dir, good_ids | bad_ids, set())
        # no-promotion：什么都不做，pending 堆积

        got, exposure = await _eval_round(rsi_dir, pack, noise_ids)
        for probe_id, passed in got.items():
            rows.append({"arm": arm, "round": r, "probe": probe_id,
                         "passed": int(passed),
                         "noise_exposed": f"{exposure:.2f}"})
        print(f"{arm} round={r} pass={sum(got.values())}/{len(got)} "
              f"noise={exposure:.2f}", flush=True)
    return rows


def _rate(sub: list[dict], prefix: str) -> str:
    cells = [int(x["passed"]) for x in sub if x["probe"].startswith(prefix)]
    return f"{sum(cells)}/{len(cells)}" if cells else "—"


def _summary(rows: list[dict], rounds: int) -> str:
    lines = [
        f"# 纵向自改进实验 v2 · {OUT.name.replace('longitudinal-', '')}",
        "",
        "10 轮合成事件流，三臂对比。stable=回归护栏（2 题），learn=当轮注入",
        "知识（10 题），噪声暴露率=stable 查询 top-5 出现错误反馈文档的比例。",
        "",
        "| 臂 | 轮次 | stable | learn | 噪声暴露 |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm in ("governed", "approve-all", "no-promotion"):
        for r in range(0, rounds + 1):
            sub = [x for x in rows if x["arm"] == arm and x["round"] == r]
            if not sub:
                continue
            lines.append(
                f"| {arm} | {r} | {_rate(sub, 'stable')} | "
                f"{_rate(sub, 'learn')} | {sub[0]['noise_exposed']} |"
            )
    lines += [
        "",
        "口径声明：合成反馈是受控信号（规则文本对应探针 needle，噪声文本与种子",
        "知识矛盾），测治理环的传导性与选择性；不是真实用户语料上的效果声明。",
        "governed 臂的「人工甄别」由脚本模拟（正确批准/噪声拒绝）。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    global OUT
    rounds = 10
    for arg in sys.argv[1:]:
        if arg.startswith("--rounds="):
            rounds = int(arg.split("=", 1)[1])
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)

    OUT.mkdir(parents=True, exist_ok=True)
    work_root = ROOT / "benchmark" / ".data" / "work" / f"longitudinal-{OUT.name}"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    rows: list[dict] = []
    for arm in ("governed", "approve-all", "no-promotion"):
        rows.extend(asyncio.run(_run_arm(arm, rounds, work_root)))

    with (OUT / "longitudinal.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["arm", "round", "probe", "passed", "noise_exposed"],
        )
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "SUMMARY.md").write_text(_summary(rows, rounds), encoding="utf-8")
    print(f"done -> {OUT / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
