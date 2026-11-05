"""真实 LLM-in-loop token 研究执行器（OpenAI 兼容端点版）。

token_study.py --prepare 生成的协议原本面向宿主侧（Cursor/Claude）人工执行；
本脚本用 OpenAI 兼容聊天接口直接执行同一协议：每题两臂（no-rsi=全量对话原文，
with-rsi=RSI 冷启动召回 top-5 命中原文），钉死模型与 temperature=0，
逐次记录 usage.prompt_tokens / completion_tokens / 墙钟到 results.csv，
随后用 `python -X utf8 benchmark/token_study.py --analyze=...` 做配对分析。

用法：
    set -a; . benchmark/.env.local; set +a
    python -X utf8 benchmark/token_study_llm.py --sample=100 --rounds=3 \
        --model=kimi-k3 --workers=4 --out=token-llm-20261105

幂等：已存在的 (qid, arm, round) 行自动跳过，中断后重跑续传。
成本护栏：no-rsi 臂每题发送整段对话（中位约 5 万 token），sample/rounds
直接决定账单，跑前先小样本（--sample=5 --rounds=1）验证单价。
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.recall_board import _locomo_samples, suppress_event_log  # noqa: E402
from benchmark.baselines.rsi_adapter import RsiAdapter  # noqa: E402

OUT = ROOT / "benchmark" / "results" / "token-llm-dev"
FIELDS = ["qid", "arm", "round", "prompt_tokens", "completion_tokens", "wall_ms"]

SYSTEM_PROMPT = (
    "You are answering questions about a long-term conversation between a user "
    "and an assistant. Use ONLY the provided context. If the context contains "
    "the answer, state it briefly; otherwise say you don't know."
)

_lock = threading.Lock()


def _load_done(csv_path: Path) -> set[tuple[str, str, str]]:
    done: set[tuple[str, str, str]] = set()
    if csv_path.exists():
        for row in csv.DictReader(csv_path.open(encoding="utf-8")):
            done.add((row["qid"], row["arm"], row["round"]))
    return done


def _stratified_sample(samples: list[dict], n: int, seed: int) -> dict[str, dict]:
    """按题型分层抽样，返回 {qid: question}，保持各类别占比。"""
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
    print(
        f"sampled {len(picked)}/{total} questions, by type: "
        + json.dumps({t: sum(1 for q in picked.values() if str(q['qtype']) == t)
                      for t in by_type}, sort_keys=True),
        flush=True,
    )
    return picked


def _call(client, model: str, max_tokens: int, context: str, question: str) -> dict:
    started = time.monotonic()
    # Kimi K3 全系强制 temperature=1（思考模型），协议的温度钉死在该端点上
    # 不可行——省略温度参数并在论文中如实披露；本研究测量 usage token，
    # 温度对输入侧 token 数无影响。
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    usage = resp.usage
    return {
        "prompt_tokens": int(usage.prompt_tokens if usage else 0),
        "completion_tokens": int(usage.completion_tokens if usage else 0),
        "wall_ms": wall_ms,
    }


def _call_anthropic(model: str, max_tokens: int, context: str, question: str) -> dict:
    """Anthropic Messages API（urllib 直连，不引 SDK）。usage 口径
    input_tokens/output_tokens 与 OpenAI 的 prompt/completion 对应。

    中转站（如 sundayrouter）按请求头识别客户端、只允许 Claude Code 身份，
    因此必须带 user-agent/x-app/anthropic-beta 三件套，鉴权用 Bearer。"""
    import urllib.request

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN", "")
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
    }).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {key}",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219",
        "user-agent": "claude-cli/2.0.5 (external, cli)",
        "x-app": "cli",
    }
    started = time.monotonic()
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            req = urllib.request.Request(
                f"{base}/v1/messages", data=body, headers=headers,
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            wall_ms = int((time.monotonic() - started) * 1000)
            usage = payload.get("usage") or {}
            return {
                "prompt_tokens": int(usage.get("input_tokens", 0)),
                "completion_tokens": int(usage.get("output_tokens", 0)),
                "wall_ms": wall_ms,
            }
        except Exception as exc:  # 重试一次后抛出，由上层记录失败
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def main() -> None:
    global OUT
    sample_n = 100
    rounds = 3
    provider = "openai"
    model = "kimi-k3"
    workers = 4
    max_tokens = 512
    for arg in sys.argv[1:]:
        if arg.startswith("--sample="):
            sample_n = int(arg.split("=", 1)[1])
        elif arg.startswith("--rounds="):
            rounds = int(arg.split("=", 1)[1])
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1]
        elif arg.startswith("--workers="):
            workers = int(arg.split("=", 1)[1])
        elif arg.startswith("--max-tokens="):
            max_tokens = int(arg.split("=", 1)[1])
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)
    if provider not in ("openai", "anthropic"):
        print(f"unknown provider: {provider}", flush=True)
        sys.exit(2)
    if provider == "anthropic" and model == "kimi-k3":
        # 官方端点默认 haiku-4-5；中转站（如 sundayrouter）只认其分组列表
        # 里的名字（claude-haiku-5-5 等），非官方 base 时改默认。
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        model = ("claude-haiku-4-5" if "api.anthropic.com" in base
                 else "claude-haiku-5-5")

    client = None
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=90, max_retries=1)
    elif not (
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    ):
        print("anthropic provider 需要 ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN",
              flush=True)
        sys.exit(2)
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "results.csv"
    done = _load_done(csv_path)

    samples = _locomo_samples()
    picked = _stratified_sample(samples, sample_n, seed=42)

    # 预建两臂上下文：no-rsi=全量原文；with-rsi=RSI top-5 命中原文
    jobs: list[dict] = []
    adapter = RsiAdapter()
    work = ROOT / "benchmark" / "data" / "work" / "token-llm"
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    adapter.open(work)
    try:
        with suppress_event_log():
            for sample in samples:
                hit_qs = [q for q in sample["questions"] if q["id"] in picked]
                if not hit_qs:
                    continue
                adapter.reset()
                adapter.ingest(sample["rows"])
                content_by_key = {k: c for k, _t, c in sample["rows"]}
                full_context = "\n".join(content_by_key.values())
                for q in hit_qs:
                    top5 = adapter.retrieve(q["question"], 5)
                    rsi_context = "\n".join(
                        content_by_key[k] for k in top5 if k in content_by_key
                    )
                    for arm, ctx in (("no-rsi", full_context),
                                     ("with-rsi", rsi_context)):
                        for r in range(1, rounds + 1):
                            if (q["id"], arm, str(r)) in done:
                                continue
                            jobs.append({
                                "qid": q["id"], "arm": arm, "round": str(r),
                                "context": ctx, "question": q["question"],
                            })
    finally:
        adapter.close()
    print(f"jobs to run: {len(jobs)} (skipped {len(done)} done)", flush=True)
    if not jobs:
        print("nothing to do", flush=True)
        return

    new = not csv_path.exists()
    sink = csv_path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(sink, fieldnames=FIELDS)
    if new:
        writer.writeheader()
        sink.flush()
    (OUT / "meta.json").write_text(json.dumps({
        "provider": provider, "model": model, "sample": sample_n,
        "rounds": rounds, "max_tokens": max_tokens,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    total_prompt = 0
    finished = 0
    started = time.monotonic()

    def run(job: dict) -> dict:
        if provider == "anthropic":
            result = _call_anthropic(model, max_tokens, job["context"], job["question"])
        else:
            result = _call(client, model, max_tokens, job["context"], job["question"])
        return {k: job[k] for k in ("qid", "arm", "round")} | result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception as exc:  # 单题失败不拖垮全局，记录后由补跑覆盖
                print(f"job failed: {exc!r}", flush=True)
                continue
            with _lock:
                writer.writerow(row)
                sink.flush()
                total_prompt += row["prompt_tokens"]
                finished += 1
                if finished % 10 == 0 or finished == len(jobs):
                    print(
                        f"{finished}/{len(jobs)} "
                        f"prompt_tokens累计={total_prompt:,} "
                        f"elapsed={time.monotonic() - started:.0f}s",
                        flush=True,
                    )
    sink.close()
    print(f"done -> {csv_path}", flush=True)


if __name__ == "__main__":
    main()
