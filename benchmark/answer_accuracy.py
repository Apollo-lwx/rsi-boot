"""答题准确率研究：检索命中 → 答对 的转化漏斗（LLM judge 口径）。

补 token 研究只测 token、不测对错的缺口。同一批 LoCoMo 分层抽样
（seed 42，与 token-llm 榜同批），三臂：
  full = 整段对话原文；rsi = RSI 冷启动 top-5；rag = vanilla-rag top-5
（bge-large 本地向量）。两臂生成器（kimi-k3 / claude-haiku-5-5）各自答题，
再由**两家交叉互判**（每份生成同时被 kimi 和 claude 判），输出准确率表 +
McNemar 配对检验。

用法：
    set -a; . benchmark/.env.local; set +a
    # 1) 生成（幂等续跑）
    python -X utf8 benchmark/answer_accuracy.py --generate --provider=openai
    python -X utf8 benchmark/answer_accuracy.py --generate --provider=anthropic
    # 2) 评判（两家 judge 各自判全部生成）
    python -X utf8 benchmark/answer_accuracy.py --judge
    # 3) 分析出表
    python -X utf8 benchmark/answer_accuracy.py --analyze

产物：<out>/generations.jsonl、judgments.jsonl、SUMMARY.md
LoCoMo category 5 为不可答题：金标准为「正确拒答」，判定时以
adversarial_answer 为参照语义、答「不知道」算对。
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.recall_board import _locomo_samples, suppress_event_log  # noqa: E402
from benchmark.baselines.rsi_adapter import RsiAdapter  # noqa: E402
from benchmark.baselines.vanilla_rag import VanillaRagAdapter  # noqa: E402

OUT = ROOT / "benchmark" / "results" / "answer-acc-dev"
ARMS = ("full", "rsi", "rag")
SAMPLE_N = 80
SEED = 42

GEN_PROMPT = (
    "You are answering questions about a long-term conversation between a user "
    "and an assistant. Use ONLY the provided context. If the context contains "
    "the answer, state it briefly; otherwise say you don't know."
)

JUDGE_PROMPT = """You are grading a candidate answer against a reference.

Question:
{question}

Reference (gold):
{reference}

Candidate answer:
{candidate}

Rules:
- verdict "correct": the candidate states the same fact as the reference
  (wording may differ); for unanswerable questions (reference says
  NOT-DISCUSSED), "correct" means the candidate declines to answer.
- verdict "incorrect": anything else, including hallucinated specifics.

Reply with ONLY a JSON object:
{{"verdict": "correct|incorrect", "rationale": "<one sentence>"}}"""

_lock = threading.Lock()


# ---------------- 抽样（与 token_study_llm 同口径，同 seed 复刻） ----------------

def _stratified_sample(samples: list[dict], n: int, seed: int) -> dict[str, dict]:
    rng = random.Random(seed)
    by_type: dict[str, list[dict]] = {}
    total = 0
    for sample in samples:
        for q in sample["questions"]:
            by_type.setdefault(str(q["qtype"]), []).append(q)
            total += 1
    picked: dict[str, dict] = {}
    for qtype, qs in sorted(by_type.items()):
        k = max(1, round(n * len(qs) / total))
        rng.shuffle(qs)
        for q in qs[:k]:
            picked[q["id"]] = q
    print(f"sampled {len(picked)}/{total}: "
          + json.dumps({t: sum(1 for q in picked.values() if str(q["qtype"]) == t)
                        for t in by_type}, sort_keys=True), flush=True)
    return picked


def _gold_answers() -> dict[str, str]:
    """qid -> 金标准文本。category 5 不可答题给 NOT-DISCUSSED 参照。"""
    raw = json.loads(
        (ROOT / "benchmark/.data/locomo10.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for conv in raw:
        sid = conv["sample_id"]
        for idx, qa in enumerate(conv["qa"]):
            qid = f"{sid}:{idx}"
            if str(qa.get("category")) == "5":
                out[qid] = ("NOT-DISCUSSED (the conversation never says; the "
                            "correct response is to decline). "
                            f"Plausible-sounding but wrong: {qa.get('adversarial_answer', '')}")
            else:
                out[qid] = str(qa.get("answer", ""))
    return out


# ---------------- 生成 ----------------

def _call_openai(client, model: str, context: str, question: str) -> dict:
    started = time.monotonic()
    resp = client.chat.completions.create(
        model=model, max_tokens=512,
        messages=[{"role": "system", "content": GEN_PROMPT},
                  {"role": "user", "content": f"{context}\n\nQuestion: {question}"}],
    )
    return {
        "answer": (resp.choices[0].message.content or "").strip(),
        "prompt_tokens": int(resp.usage.prompt_tokens if resp.usage else 0),
        "completion_tokens": int(resp.usage.completion_tokens if resp.usage else 0),
        "wall_ms": int((time.monotonic() - started) * 1000),
    }


def _call_anthropic(model: str, context: str, question: str) -> dict:
    import urllib.request

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN", "")
    body = json.dumps({
        "model": model, "max_tokens": 512, "system": GEN_PROMPT,
        "messages": [{"role": "user",
                      "content": f"{context}\n\nQuestion: {question}"}],
    }).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {key}", "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219",
        "user-agent": "claude-cli/2.0.5 (external, cli)", "x-app": "cli",
    }
    started = time.monotonic()
    req = urllib.request.Request(f"{base}/v1/messages", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    usage = payload.get("usage") or {}
    return {
        "answer": "".join(b.get("text", "")
                          for b in payload.get("content", [])).strip(),
        "prompt_tokens": int(usage.get("input_tokens", 0)),
        "completion_tokens": int(usage.get("output_tokens", 0)),
        "wall_ms": int((time.monotonic() - started) * 1000),
    }


def _generate(provider: str, model: str | None, workers: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    gen_path = OUT / "generations.jsonl"
    done: set[tuple[str, str]] = set()
    if gen_path.exists():
        for line in gen_path.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done.add((r["qid"], r["arm"], r["provider"]))
    samples = _locomo_samples()
    picked = _stratified_sample(samples, SAMPLE_N, SEED)

    jobs: list[dict] = []
    rsi = RsiAdapter()
    rag = VanillaRagAdapter()
    work = ROOT / "benchmark" / "data" / "work" / "answer-acc"
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    rsi.open(work / "rsi")
    rag.open(work / "rag")
    try:
        with suppress_event_log():
            for sample in samples:
                hit_qs = [q for q in sample["questions"] if q["id"] in picked]
                if not hit_qs:
                    continue
                rsi.reset()
                rag.reset()
                rsi.ingest(sample["rows"])
                rag.ingest(sample["rows"])
                content = {k: c for k, _t, c in sample["rows"]}
                full_ctx = "\n".join(content.values())
                for q in hit_qs:
                    ctxs = {
                        "full": full_ctx,
                        "rsi": "\n".join(content[k] for k in rsi.retrieve(q["question"], 5) if k in content),
                        "rag": "\n".join(content[k] for k in rag.retrieve(q["question"], 5) if k in content),
                    }
                    for arm, ctx in ctxs.items():
                        if (q["id"], arm, provider) in done:
                            continue
                        jobs.append({"qid": q["id"], "qtype": str(q["qtype"]),
                                     "arm": arm, "context": ctx,
                                     "question": q["question"]})
    finally:
        rsi.close()
        rag.close()
    print(f"to generate: {len(jobs)} (skipped {len(done)} done)", flush=True)
    if not jobs:
        return

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=90, max_retries=1)
        model = model or "kimi-k3"
    else:
        client = None
        model = model or "claude-haiku-5-5"

    sink = gen_path.open("a", encoding="utf-8")
    finished = 0

    def run(job: dict) -> dict:
        if provider == "anthropic":
            r = _call_anthropic(model, job["context"], job["question"])
        else:
            r = _call_openai(client, model, job["context"], job["question"])
        return {"qid": job["qid"], "qtype": job["qtype"], "arm": job["arm"],
                "provider": provider, "model": model, **r}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception as exc:
                print(f"gen failed: {exc!r}", flush=True)
                continue
            with _lock:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                finished += 1
                if finished % 20 == 0 or finished == len(jobs):
                    print(f"{finished}/{len(jobs)}", flush=True)
    sink.close()
    print(f"done -> {gen_path}", flush=True)


# ---------------- 评判 ----------------

def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    return json.loads(raw)


def _judge_openai(client, model: str, question: str, reference: str,
                  candidate: str) -> dict:
    resp = client.chat.completions.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, candidate=candidate)}],
    )
    return _parse_json((resp.choices[0].message.content or "").strip())


def _judge_anthropic(model: str, question: str, reference: str,
                     candidate: str) -> dict:
    import urllib.request

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN", "")
    body = json.dumps({
        "model": model, "max_tokens": 2000,
        "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, candidate=candidate)}],
    }).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {key}", "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219",
        "user-agent": "claude-cli/2.0.5 (external, cli)", "x-app": "cli",
    }
    req = urllib.request.Request(f"{base}/v1/messages", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return _parse_json("".join(b.get("text", "")
                               for b in payload.get("content", [])).strip())


def _judge(workers: int, only: set[str] | None = None) -> None:
    gen_path = OUT / "generations.jsonl"
    jud_path = OUT / "judgments.jsonl"
    gens = [json.loads(l) for l in gen_path.open(encoding="utf-8") if l.strip()]
    golds = _gold_answers()
    # 生成行未冗余存 question；按 qid 从样本集查回
    qtext: dict[str, str] = {}
    for sample in _locomo_samples():
        for q in sample["questions"]:
            qtext[q["id"]] = q["question"]
    done: set[tuple] = set()
    if jud_path.exists():
        for line in jud_path.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done.add((r["qid"], r["arm"], r["gen_provider"], r["judge"]))
    judges = [("kimi", "openai", "kimi-k3"), ("claude", "anthropic",
                                               "claude-haiku-5-5")]
    if only:
        judges = [j for j in judges if j[0] in only]
    jobs = []
    for g in gens:
        for judge_name, _prov, _model in judges:
            key = (g["qid"], g["arm"], g["provider"], judge_name)
            if key in done:
                continue
            jobs.append({"gen": g, "judge": judge_name})
    print(f"to judge: {len(jobs)} (skipped {len(done)} done)", flush=True)
    if not jobs:
        return

    from openai import OpenAI

    oai = OpenAI(timeout=90, max_retries=1)
    sink = jud_path.open("a", encoding="utf-8")
    finished = 0

    def run(job: dict) -> dict:
        g = job["gen"]
        question = g.get("question") or qtext.get(g["qid"], "")
        ref = golds.get(g["qid"], "")
        if job["judge"] == "claude":
            r = _judge_anthropic("claude-haiku-5-5", question, ref,
                                 g["answer"])
        else:
            r = _judge_openai(oai, "kimi-k3", question, ref, g["answer"])
        verdict = str(r.get("verdict", "")).strip()
        if verdict not in ("correct", "incorrect"):
            verdict = "incorrect"
        return {"qid": g["qid"], "qtype": g["qtype"], "arm": g["arm"],
                "gen_provider": g["provider"], "judge": job["judge"],
                "verdict": verdict,
                "rationale": str(r.get("rationale", ""))[:300]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception as exc:
                print(f"judge failed: {exc!r}", flush=True)
                continue
            with _lock:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                finished += 1
                if finished % 40 == 0 or finished == len(jobs):
                    print(f"{finished}/{len(jobs)}", flush=True)
    sink.close()
    print(f"done -> {jud_path}", flush=True)


# ---------------- 分析 ----------------

def _mcnemar_exact(b: int, c: int) -> float:
    """McNemar 精确双侧 p（b=c=0 时 p=1）。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _analyze() -> None:
    jud_path = OUT / "judgments.jsonl"
    rows = [json.loads(l) for l in jud_path.open(encoding="utf-8") if l.strip()]
    # 双 judge 一致率
    by_key: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in rows:
        by_key[(r["qid"], r["arm"], r["gen_provider"])][r["judge"]] = r["verdict"]
    agree = sum(1 for v in by_key.values() if len(v) == 2 and len(set(v.values())) == 1)
    both = sum(1 for v in by_key.values() if len(v) == 2)
    if both:
        print(f"judge 一致率: {agree}/{both} = {agree / both:.3f}")
    else:
        nj = len({r["judge"] for r in rows})
        print(f"judge 一致率: 单 judge 阶段（{nj} 家），待双判后复算")

    # 合并口径：双 judge 多数（2 判 1 定时取一致项；不一致记 incorrect，保守）
    merged: dict[tuple, str] = {}
    for key, v in by_key.items():
        vals = list(v.values())
        merged[key] = "correct" if vals and all(x == "correct" for x in vals) else "incorrect"

    # 准确率表：gen_provider × arm
    acc: dict[tuple, list[int]] = defaultdict(list)
    for (qid, arm, prov), verdict in merged.items():
        acc[(prov, arm)].append(1 if verdict == "correct" else 0)
    print("\n准确率（双 judge 全对才算对，保守合并）:")
    for prov in sorted({k[0] for k in acc}):
        line = [prov]
        for arm in ARMS:
            v = acc.get((prov, arm), [])
            line.append(f"{arm}: {sum(v)}/{len(v)} = {sum(v) / max(1, len(v)):.3f}")
        print("  " + " | ".join(line))

    # McNemar：rsi vs full、rsi vs rag（按 gen_provider 分开）
    print("\nMcNemar（精确双侧）:")
    for prov in sorted({k[0] for k in acc}):
        for other in ("full", "rag"):
            qs = {qid for (qid, arm, p) in merged if p == prov and arm == "rsi"} & \
                 {qid for (qid, arm, p) in merged if p == prov and arm == other}
            b = sum(1 for q in qs
                    if merged[(q, "rsi", prov)] == "correct"
                    and merged[(q, other, prov)] != "correct")
            c = sum(1 for q in qs
                    if merged[(q, "rsi", prov)] != "correct"
                    and merged[(q, other, prov)] == "correct")
            print(f"  {prov}: rsi vs {other}: b={b} c={c} "
                  f"p={_mcnemar_exact(b, c):.4f}")

    # 按题型（合并两个生成器）
    print("\n按题型（两生成器合并）:")
    by_qt: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    qtype_of = {r["qid"]: r["qtype"] for r in rows}
    for (qid, arm, prov), verdict in merged.items():
        by_qt[qtype_of.get(qid, "?")][arm].append(1 if verdict == "correct" else 0)
    for qt in sorted(by_qt):
        parts = []
        for arm in ARMS:
            v = by_qt[qt].get(arm, [])
            if v:
                parts.append(f"{arm} {sum(v)}/{len(v)}={sum(v)/len(v):.2f}")
        print(f"  {qt}: " + " | ".join(parts))


def main() -> None:
    global OUT
    generate = judge = analyze = False
    provider = "openai"
    model = None
    workers = 4
    judges_only: set[str] | None = None
    for arg in sys.argv[1:]:
        if arg == "--generate":
            generate = True
        elif arg == "--judge":
            judge = True
        elif arg == "--analyze":
            analyze = True
        elif arg.startswith("--judges="):
            judges_only = {s.strip() for s in arg.split("=", 1)[1].split(",") if s.strip()}
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1]
        elif arg.startswith("--workers="):
            workers = int(arg.split("=", 1)[1])
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)
    if generate:
        _generate(provider, model, workers)
    elif judge:
        _judge(workers, judges_only)
    elif analyze:
        _analyze()
    else:
        print(__doc__, flush=True)


if __name__ == "__main__":
    main()
