"""EngMem-Bench AI 第二标注者：独立 LLM 核验协议。

对 annotation_pack.py 导出的标注单逐题核验：取 bench.yaml 里 gold 锚点
指向的语料节原文，连同 question/reference_answer 一起交给独立 LLM
（Kimi K3，OpenAI 兼容端点，与作者标注链路无关）做四分类裁决：
correct / wrong-answer / wrong-anchor / unclear。

产物：
- sheet_annotator.csv    与 sheet.csv 同构（verdict/annotator_answer 已填）
- sheet_annotator.rationales.jsonl   每题的判定理由（审稿备查）

用法：
    set -a; . benchmark/.env.local; set +a
    python -X utf8 benchmark/engmem/ai_annotator.py \
        --sheet=benchmark/results/engmem-annotation/sheet.csv

幂等：已写入 sheet_annotator.csv 的 qid 自动跳过，中断可续跑。
分歧题随后由作者人工仲裁，用 annotation_pack.py --kappa 算一致性。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BENCH = Path(__file__).resolve().parent / "bench.yaml"
FIELDS = [
    "qid", "repo", "type", "difficulty", "question",
    "reference_answer", "gold_anchor", "verdict", "annotator_answer",
]
VERDICTS = ("correct", "wrong-answer", "wrong-anchor", "unclear")

PROMPT = """You are verifying one item of an engineering-memory benchmark.

Question:
{question}

Reference answer:
{answer}

Gold anchor section(s), verbatim from the pinned repository snapshot:
{sections}

Judge the item:
- correct: the reference answer is supported by the gold section(s).
- wrong-answer: the section(s) do NOT support the reference answer (the anchor is right, the answer is wrong).
- wrong-anchor: the anchor points to the wrong section (the answer may be true elsewhere).
- unclear: the question is ambiguous or the section content is insufficient to judge.

Reply with ONLY a JSON object:
{{"verdict": "correct|wrong-answer|wrong-anchor|unclear",
  "annotator_answer": "<correct answer if verdict is wrong-*, else empty>",
  "rationale": "<one sentence citing the decisive evidence>"}}"""

_lock = threading.Lock()


def _load_sheet(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _bench_index() -> tuple[dict[str, dict], dict[str, str]]:
    """返回 {qid: question记录} 与 {anchor_id: section原文}。"""
    import yaml

    data = yaml.safe_load(BENCH.read_text(encoding="utf-8"))
    questions: dict[str, dict] = {}
    sections: dict[str, str] = {}
    for repo in data["repos"]:
        for q in repo["questions"]:
            questions[q["id"]] = q
        for c in repo["corpus"]:
            sections[c["id"]] = c["content"]
    return questions, sections


def _judge(client, model: str, question: str, answer: str, section_text: str) -> dict:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2000,  # K3 是思考型模型，推理过程计入输出预算，要给足
        messages=[{"role": "user", "content": PROMPT.format(
            question=question, answer=answer, sections=section_text)}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_verdict(raw)


def _parse_verdict(raw: str) -> dict:
    raw = raw.strip()
    # 容忍模型包裹 ```json 代码块
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    obj = json.loads(raw)
    verdict = str(obj.get("verdict", "")).strip()
    if verdict not in VERDICTS:
        verdict = "unclear"
    return {
        "verdict": verdict,
        "annotator_answer": str(obj.get("annotator_answer") or "").strip(),
        "rationale": str(obj.get("rationale") or "").strip(),
    }


def _judge_anthropic(model: str, question: str, answer: str, section_text: str) -> dict:
    """Claude 通道：Anthropic Messages 兼容端点（中转站按 Claude Code
    客户端头放行，同 token_study_llm.py 的口径）。"""
    import urllib.request

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN", "")
    body = json.dumps({
        "model": model,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": PROMPT.format(
            question=question, answer=answer, sections=section_text)}],
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
    req = urllib.request.Request(
        f"{base}/v1/messages", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw = "".join(b.get("text", "") for b in payload.get("content", []))
    return _parse_verdict(raw)


def main() -> None:
    sheet = None
    provider = "openai"
    model = "kimi-k3"
    workers = 4
    for arg in sys.argv[1:]:
        if arg.startswith("--sheet="):
            sheet = Path(arg.split("=", 1)[1])
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1]
        elif arg.startswith("--workers="):
            workers = int(arg.split("=", 1)[1])
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)
    if sheet is None:
        print("need --sheet=<annotation sheet csv>", flush=True)
        sys.exit(2)
    if provider not in ("openai", "anthropic"):
        print(f"unknown provider: {provider}", flush=True)
        sys.exit(2)
    if provider == "anthropic" and model == "kimi-k3":
        model = "claude-haiku-5-5"

    rows = _load_sheet(sheet)
    suffix = "" if provider == "openai" else f"_{provider}"
    out_csv = sheet.with_name(f"sheet_annotator{suffix}.csv")
    out_rat = sheet.with_name(f"sheet_annotator{suffix}.rationales.jsonl")

    done: set[str] = set()
    if out_csv.exists():
        done = {r["qid"] for r in _load_sheet(out_csv)}

    questions, sections = _bench_index()
    jobs: list[dict] = []
    for row in rows:
        if row["qid"] in done:
            continue
        q = questions.get(row["qid"])
        if q is None:
            print(f"qid 不在 bench.yaml: {row['qid']}", flush=True)
            continue
        parts = []
        for anchor in q.get("gold") or []:
            text = sections.get(anchor)
            parts.append(f"[{anchor}]\n{text}" if text
                         else f"[{anchor}] <anchor not found in corpus>")
        jobs.append({"row": row, "sections": "\n\n".join(parts)})
    print(f"to judge: {len(jobs)} (skipped {len(done)} done)", flush=True)
    if not jobs:
        return

    from openai import OpenAI

    client = OpenAI(timeout=90, max_retries=1) if provider == "openai" else None

    new = not out_csv.exists()
    csv_f = out_csv.open("a", encoding="utf-8", newline="")
    rat_f = out_rat.open("a", encoding="utf-8")
    writer = csv.DictWriter(csv_f, fieldnames=FIELDS)
    if new:
        writer.writeheader()
        csv_f.flush()

    finished = 0

    def run(job: dict) -> tuple[dict, dict]:
        row = job["row"]
        if provider == "anthropic":
            verdict = _judge_anthropic(model, row["question"],
                                       row["reference_answer"], job["sections"])
        else:
            verdict = _judge(client, model, row["question"],
                             row["reference_answer"], job["sections"])
        out = {k: row[k] for k in FIELDS}
        out["verdict"] = verdict["verdict"]
        out["annotator_answer"] = verdict["annotator_answer"]
        rat = {"qid": row["qid"], "verdict": verdict["verdict"],
               "rationale": verdict["rationale"]}
        return out, rat

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for fut in as_completed(futures):
            try:
                out, rat = fut.result()
            except Exception as exc:
                print(f"judge failed: {exc!r}", flush=True)
                continue
            with _lock:
                writer.writerow(out)
                csv_f.flush()
                rat_f.write(json.dumps(rat, ensure_ascii=False) + "\n")
                rat_f.flush()
                finished += 1
                if finished % 10 == 0 or finished == len(jobs):
                    print(f"{finished}/{len(jobs)}", flush=True)
    csv_f.close()
    rat_f.close()

    # 分布汇总
    final = _load_sheet(out_csv)
    from collections import Counter

    dist = Counter(r["verdict"] for r in final)
    print("verdict 分布:", json.dumps(dict(dist), ensure_ascii=False), flush=True)
    print(f"done -> {out_csv}", flush=True)


if __name__ == "__main__":
    main()
