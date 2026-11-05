"""新基准脚本的核心逻辑测试：engmem 锚点、token 统计、纵向/投毒/规模。

全部走小样本与临时目录，不依赖网络与外部 Key。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.token_study import wilcoxon_signed_rank  # noqa: E402


# ---------- token_study 的统计检验 ----------

def test_wilcoxon_clear_separation():
    diffs = [float(d) for d in range(10, 110, 10)]  # 全正
    stat = wilcoxon_signed_rank(diffs)
    assert stat["n"] == 10
    assert stat["z"] is not None and stat["z"] > 0
    assert stat["p_two_sided"] is not None and stat["p_two_sided"] < 0.01
    assert stat["r"] is not None and stat["r"] > 0.5


def test_wilcoxon_all_zero():
    stat = wilcoxon_signed_rank([0.0, 0.0])
    assert stat["n"] == 0
    assert stat["z"] is None and stat["p_two_sided"] is None


def test_wilcoxon_symmetric_noise():
    diffs = [5.0, -5.0, 3.0, -3.0, 1.0, -1.0]
    stat = wilcoxon_signed_rank(diffs)
    assert stat["n"] == 6
    assert stat["p_two_sided"] is not None and stat["p_two_sided"] > 0.5


# ---------- engmem 语料与锚点 ----------

def _bench():
    path = ROOT / "benchmark" / "engmem" / "bench.yaml"
    if not path.is_file():
        pytest.skip("bench.yaml 未构建")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_engmem_gold_anchors_resolve():
    """每题 gold 必须存在于语料 id 集合（构建期质检的回归护栏）。"""
    data = _bench()
    n_questions = 0
    for repo in data["repos"]:
        ids = {entry["id"] for entry in repo["corpus"]}
        for question in repo["questions"]:
            n_questions += 1
            assert question["gold"], f"{question['id']} 无 gold"
            for gold in question["gold"]:
                assert gold in ids, f"{question['id']} 锚点失效: {gold}"
            assert question["type"] in ("convention", "prohibition", "fix")
            assert question.get("difficulty") in ("easy", "medium", "hard")
    assert n_questions >= 300


def test_engmem_repo_count_and_license():
    data = _bench()
    assert len(data["repos"]) == 21
    for repo in data["repos"]:
        assert repo.get("license"), f"{repo['name']} 缺许可证标注"
        assert repo.get("commit"), f"{repo['name']} 未钉 commit"


# ---------- 已出榜结果的结构完整性 ----------

@pytest.mark.parametrize("name", [
    "engmem-20261105",
    "engmem-emb-20261105",
    "baselines-20261103",
    "baselines-20261104",
    "recall-20261104",
    "longitudinal-20261103",
    "longitudinal-20261104",
    "adversarial-20261103",
    "scalability-20261103",
    "token-20261104",
    "ablation-20261104",
    "contextfix-20261104",
    "baselines-emb-20261104",
    "engmem-annotation",
    "token-llm-20261105",
    "token-llm-claude-20261105",
    "answer-acc-20261105",
    "cross-host",
])
def test_result_boards_exist(name: str):
    board = ROOT / "benchmark" / "results" / name / "SUMMARY.md"
    assert board.is_file(), f"缺榜: {name}"


def test_ablation_full_matches_main_board():
    """消融榜 full 配置的 LoCoMo 数字必须与官方主榜一致（一致性校验）。"""
    import json

    main = json.loads((ROOT / "benchmark/results/recall-20261104/summary.json")
                      .read_text(encoding="utf-8"))
    abl = ROOT / "benchmark/results/ablate-full/summary.json"
    if not abl.is_file():
        pytest.skip("ablate-full 未跑")
    full = json.loads(abl.read_text(encoding="utf-8"))
    assert full["datasets"]["LoCoMo"]["hit@5"] == pytest.approx(
        main["datasets"]["LoCoMo"]["hit@5"], abs=1e-9,
    )


def test_longitudinal_arm_contrast():
    """治理臂传导信号且不暴露噪声；无门臂噪声暴露率 1.0；消融臂不学。"""
    import csv

    path = ROOT / "benchmark" / "results" / "longitudinal-20261104" / "longitudinal.csv"
    if not path.is_file():
        pytest.skip("longitudinal v2 csv 未生成")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    last_round = max(int(r["round"]) for r in rows)

    gov = [r for r in rows if r["arm"] == "governed" and int(r["round"]) == last_round]
    gov_learn = [int(r["passed"]) for r in gov if r["probe"].startswith("learn")]
    assert gov_learn and sum(gov_learn) >= 8, "治理臂 learn 应接近满分"

    gov_noise = {float(r["noise_exposed"]) for r in rows if r["arm"] == "governed"}
    assert gov_noise == {0.0}, "治理臂全程不应有噪声暴露"

    aa = [r for r in rows
          if r["arm"] == "approve-all" and int(r["round"]) >= 3]
    assert aa and all(float(r["noise_exposed"]) == 1.0 for r in aa), \
        "无门臂自首轮噪声起暴露率应为 1.0"

    abl = [r for r in rows
           if r["arm"] == "no-promotion" and int(r["round"]) == last_round
           and r["probe"].startswith("learn")]
    assert abl and all(r["passed"] == "0" for r in abl), "消融臂不应学到任何规则"


def test_adversarial_all_filtered():
    import json

    path = ROOT / "benchmark" / "results" / "adversarial-20261103" / "results.json"
    if not path.is_file():
        pytest.skip("adversarial results 未生成")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["A1_pending_poison_filtered"] is True
    assert data["A2_forged_approval_filtered"] is True
    assert data["A3_keyword_stuffing_first_is_legit"] is True
    assert all(data["stable"].values())


# ---------- LoCoMo 上下文增强 v2（temporal 挤位修复） ----------


def test_locomo_context_v2_no_foreign_dates():
    """v2 附带的上下文不得引入外来时间锚点：无 [date] 前缀、无内嵌日期。"""
    from benchmark.recall_board import _locomo_samples
    from rsi_boot.rag.temporal import extract_iso_dates

    samples = _locomo_samples(context=True, context_mode="v2")
    n_ctx = 0
    for sample in samples:
        for _gold, _title, content in sample["rows"]:
            if "\ncontext: " not in content:
                continue
            n_ctx += 1
            ctx = content.split("\ncontext: ", 1)[1]
            assert not ctx.lstrip().startswith("["), f"context 行带日期前缀: {ctx[:80]}"
            assert not extract_iso_dates(ctx), f"context 行含内嵌日期: {ctx[:80]}"
    assert n_ctx > 0, "v2 下应仍有上下文增强的文档"


def test_locomo_context_modes_differ():
    """v1 与 v2 写出的语料必须不同（否则修复没有生效面）。"""
    from benchmark.recall_board import _locomo_samples

    v1 = _locomo_samples(context=True, context_mode="v1")
    v2 = _locomo_samples(context=True, context_mode="v2")
    differ = any(
        r1[2] != r2[2]
        for s1, s2 in zip(v1, v2)
        for r1, r2 in zip(s1["rows"], s2["rows"])
    )
    assert differ


def test_vanilla_rag_embed_model_env_override(monkeypatch):
    """embedding 模型名可被 RSI_BASELINE_EMBED_MODEL 覆盖（OpenAI 兼容端点如 Kimi），
    默认仍是 text-embedding-3-small。"""
    from benchmark.baselines.vanilla_rag import _embed_model

    monkeypatch.delenv("RSI_BASELINE_EMBED_MODEL", raising=False)
    assert _embed_model() == "text-embedding-3-small"
    monkeypatch.setenv("RSI_BASELINE_EMBED_MODEL", "moonshot-v3-embedding")
    assert _embed_model() == "moonshot-v3-embedding"


def test_vanilla_rag_notes_report_configured_model(monkeypatch):
    """无 Key 时 notes 标注 hashing 下界；有 Key 时标注实际配置的模型名。"""
    from benchmark.baselines.vanilla_rag import VanillaRagAdapter

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = VanillaRagAdapter()
    adapter.open(Path("."))
    assert "hashing-bow" in adapter.notes()["embedding"]

    # 有 Key 但强制 hashing 档时仍须如实标注 fallback
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    forced = VanillaRagAdapter(force_hashing=True)
    forced.open(Path("."))
    assert "hashing-bow" in forced.notes()["embedding"]


def test_mem0_config_compatible_endpoint(monkeypatch):
    """无 OPENAI_BASE_URL 时走 mem0 原生默认（空配置）；指向 OpenAI 兼容端点时
    显式给出聊天/向量模型名，且均可被环境变量覆盖。"""
    from benchmark.baselines.mem0_adapter import _mem0_config

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert _mem0_config() == {}

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.moonshot.cn/v1")
    monkeypatch.delenv("RSI_MEM0_LLM_MODEL", raising=False)
    monkeypatch.setenv("RSI_BASELINE_EMBED_MODEL", "moonshot-v3-embedding")
    cfg = _mem0_config()
    assert cfg["llm"]["config"]["model"] == "kimi-k2.6"
    assert cfg["embedder"]["config"]["model"] == "moonshot-v3-embedding"
    assert cfg["llm"]["config"]["openai_base_url"].endswith("moonshot.cn/v1")


def test_embedding_baseline_board_numbers():
    """向量基线榜钉住关键事实：bge-large 在 LoCoMo 反超 rsi、在 EngMem 仍落后，
    防止论文数字与榜单漂移。"""
    import json

    base = json.loads(
        (ROOT / "benchmark/results/baselines-emb-20261104/summary.json")
        .read_text(encoding="utf-8")
    )
    loc = base["datasets"]["LoCoMo"]
    assert loc["vanilla-rag"]["hit@5"] == pytest.approx(0.686, abs=0.002)
    assert loc["rsi"]["hit@5"] == pytest.approx(0.620, abs=0.002)
    assert "bge-large" in base["notes"]["vanilla-rag"]["embedding"]
    assert "hashing" in base["notes"]["vanilla-rag"]["embedding"]  # LME 仍是下界

    eng = json.loads(
        (ROOT / "benchmark/results/engmem-emb-20261105/summary.json")
        .read_text(encoding="utf-8")
    )
    assert (
        eng["systems"]["rsi"]["overall"]["hit@5"]
        > eng["systems"]["vanilla-rag"]["overall"]["hit@5"]
    )
    # 锚点修复后钉数：rsi 93.0% vs vanilla 87.3%，hard 难度 rsi 满分
    assert eng["systems"]["rsi"]["overall"]["hit@5"] == pytest.approx(
        0.930, abs=0.002)
    assert eng["systems"]["vanilla-rag"]["overall"]["hit@5"] == pytest.approx(
        0.873, abs=0.002)


def test_engmem_annotation_panel_artifacts():
    """三模型标注团产物：87 题三份裁决表 + 作者仲裁表，verdict 合法。"""
    import csv as _csv

    d = ROOT / "benchmark/results/engmem-annotation"
    sheets = ["sheet.csv", "sheet_annotator.csv", "sheet_annotator_anthropic.csv",
              "sheet_annotator_cursor.csv", "sheet_author.csv"]
    legal = {"correct", "wrong-answer", "wrong-anchor", "unclear", ""}
    qid_sets = []
    for name in sheets:
        rows = list(_csv.DictReader((d / name).open(encoding="utf-8-sig")))
        assert len(rows) == 87, f"{name}: {len(rows)} 行"
        qid_sets.append({r["qid"] for r in rows})
        if name != "sheet.csv":
            bad = {r["verdict"] for r in rows} - legal
            assert not bad, f"{name} 非法 verdict: {bad}"
    assert len(set(frozenset(s) for s in qid_sets)) == 1, "各表 qid 集合不一致"
    author = {r["qid"]: r["verdict"] for r in _csv.DictReader(
        (d / "sheet_author.csv").open(encoding="utf-8-sig"))}
    flagged = {q for q, v in author.items() if v != "correct"}
    assert len(flagged) == 5, f"作者仲裁应确认 5 处缺陷: {len(flagged)}"


def test_token_llm_board_pairs_and_reduction():
    """LLM 实测 token 榜：160 对完整配对，两臂中位压缩比必须 <2%。"""
    import csv as _csv
    import statistics as _stats

    path = ROOT / "benchmark/results/token-llm-20261105/results.csv"
    rows = list(_csv.DictReader(path.open(encoding="utf-8")))
    paired = {}
    for row in rows:
        paired.setdefault((row["qid"], row["round"]), {})[row["arm"]] = int(
            row["prompt_tokens"]
        )
    full = [a for a in paired.values() if "no-rsi" in a and "with-rsi" in a]
    assert len(full) >= 150, f"配对不足: {len(full)}"
    no = [a["no-rsi"] for a in full]
    rsi = [a["with-rsi"] for a in full]
    assert all(r < n for n, r in zip(no, rsi)), "存在 with-rsi 不小于 no-rsi 的对"
    ratio = _stats.median(rsi) / _stats.median(no)
    assert ratio < 0.02, f"中位压缩比异常: {ratio}"


def test_token_llm_claude_board_pairs_and_reduction():
    """Claude 复现榜：160 对完整配对；中转网关注入约 1.9k 固定开销，
    故阈值放宽到中位压缩比 <5%（Kimi 轮为 <2%）。"""
    import csv as _csv
    import statistics as _stats

    path = ROOT / "benchmark/results/token-llm-claude-20261105/results.csv"
    rows = list(_csv.DictReader(path.open(encoding="utf-8")))
    paired = {}
    for row in rows:
        paired.setdefault((row["qid"], row["round"]), {})[row["arm"]] = int(
            row["prompt_tokens"]
        )
    full = [a for a in paired.values() if "no-rsi" in a and "with-rsi" in a]
    assert len(full) >= 150, f"配对不足: {len(full)}"
    no = [a["no-rsi"] for a in full]
    rsi = [a["with-rsi"] for a in full]
    assert all(r < n for n, r in zip(no, rsi)), "存在 with-rsi 不小于 no-rsi 的对"
    ratio = _stats.median(rsi) / _stats.median(no)
    assert ratio < 0.05, f"中位压缩比异常: {ratio}"


def test_cross_host_cursor_perfect_agreement():
    """跨宿主榜：Cursor 与 Claude Code 实测均与 RSI 两次自读基座完全一致。"""
    import json as _json

    board = ROOT / "benchmark/results/cross-host"
    for host in ("cursor", "claude"):
        rows = [_json.loads(line) for line in
                (board / f"host-{host}.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert len(rows) == 5, f"{host} 侧应覆盖 5 个查询: {len(rows)}"
        for base_name in ("host-rsi-a.jsonl", "host-rsi-b.jsonl"):
            base = {_json.loads(line)["qid"]: _json.loads(line)["ids"] for line in
                    (board / base_name).read_text(encoding="utf-8").splitlines()
                    if line.strip()}
            for row in rows:
                assert row["ids"] == base[row["qid"]], (
                    f"{base_name} 在 {row['qid']} 上与 {host} 不一致"
                )


def test_answer_acc_board_coverage_and_abstention():
    """答题准确率榜：480 生成 × 双 judge 全覆盖；rsi 拒答可靠性必须最高。"""
    import json as _json
    from collections import defaultdict as _dd

    board = ROOT / "benchmark/results/answer-acc-20261105"
    gens = [_json.loads(l) for l in
            (board / "generations.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert len(gens) == 480, f"生成应为 480 份: {len(gens)}"
    juds = [_json.loads(l) for l in
            (board / "judgments.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert len(juds) == 960, f"判定应为 960 份: {len(juds)}"
    # 双 judge 保守合并
    by_key = _dd(dict)
    for r in juds:
        by_key[(r["qid"], r["arm"], r["gen_provider"])][r["judge"]] = r["verdict"]
    assert all(len(v) == 2 for v in by_key.values()), "存在缺判的生成"
    merged = {k: ("correct" if all(x == "correct" for x in v.values())
                  else "incorrect") for k, v in by_key.items()}
    # 对抗题（不可答）三臂对比：rsi 拒答最可靠
    qtype = {r["qid"]: r["qtype"] for r in juds}
    adv = [k for k in merged if qtype.get(k[0]) == "adversarial"]
    acc = {arm: sum(1 for k in adv if k[1] == arm and merged[k] == "correct")
           / max(1, sum(1 for k in adv if k[1] == arm))
           for arm in ("full", "rsi", "rag")}
    assert acc["rsi"] > acc["rag"], f"rsi 拒答应优于 rag: {acc}"
    assert acc["rsi"] > acc["full"], f"rsi 拒答应优于 full: {acc}"
