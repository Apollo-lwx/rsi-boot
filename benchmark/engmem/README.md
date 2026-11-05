# EngMem-Bench：工程记忆检索基准

面向「软件工程记忆」的检索评测基准。现有记忆基准（LoCoMo、LongMemEval）
测的是对话记忆；工程场景里 agent 需要记住的是另一类东西：项目的开发规
范、明令禁止的做法、历史 bug 的修法。EngMem-Bench 专门测这三类。

## 数据

- **21 个真实开源 Python 仓库**（MIT/Apache/BSD 许可证，允许以片段形式再分发），
  每个仓库浅克隆并钉死 commit（`repos.lock.yaml`）。
- 语料来自仓库内真实的 agent 可见文档：`AGENTS.md` / `CLAUDE.md` /
  `AI_POLICY.md` / `CONTRIBUTING` / `SECURITY` / `CODE_OF_CONDUCT` /
  `README` / `docs/**` 及 changelog（`CHANGES` / `HISTORY` / `NEWS` /
  release notes 等）。
- **315 道人工编写题**，分三类：
  - `convention`（144）：项目惯例与流程（在哪提问、分支模型、提交信息格式…）
  - `prohibition`（78）：明文禁令（禁止 AI 生成 PR、禁止改生成代码、
    禁止无测试合入、安全披露纪律…）
  - `fix`（93）：历史修复知识（某版本修了哪个 bug、行为变更、弃用…）
- 难度分层：easy 81 / medium 190 / hard 44。

## 锚点与质检

每题的 gold 是 `"文件::节标题"` 形式的锚点（同名节用 `@N` 消歧），构建期
解析到具体语料条目，**解析失败即构建失败**——题目漂移（仓库更新导致锚
点失效）在第一时间暴露，而不是悄悄变成错误标签。`build.py` 负责：

1. 从 `repos.lock.yaml` 钉版的克隆中抽语料（markdown/rst 标题切节，
   fence 跟踪只对 `.md`；rst 的 `~~~` 下划线不当 fence）。
2. 把 `authored/<repo>.yaml` 的人工题解析到语料条目 id。
3. 全部锚点解析通过才产出 `bench.yaml`。

## 跑榜

```bash
# 采集（需要网络，浅克隆 21 个仓库）
python -X utf8 benchmark/engmem/collect.py

# 构建 bench.yaml（锚点质检不通过会报错退出）
python -X utf8 benchmark/engmem/build.py

# 跑榜（系统复用 benchmark/run_baselines.py 的适配器）
python -X utf8 benchmark/engmem/run.py --systems=rsi,vanilla-rag,full-context \
    --out=engmem-20261105
```

指标与召回主榜完全同口径（Hit@5 / Recall@5 / MRR / Hit@10 / Recall@10，
检索命中而非模型答题分），按题型与难度分组出表。
最新结果见 `../results/engmem-20261105/SUMMARY.md`（hashing 基线）与
`../results/engmem-emb-20261105/SUMMARY.md`（bge-large 向量基线主榜）。

## 标注质量审计（2026-11-05）

三模型核验团（Kimi K3 / Claude Haiku / GLM 5.2，各自独立端点、同一固定
核验协议）对 87 题分层样本逐题核对锚点节原文：84/87 三家全票一致，
Fleiss' κ = 0.738；对作者仲裁结论的 Cohen's κ 分别为 0.767 / 0.808 / 0.883。
审计发现 5 处真实缺陷（3 个版本错配锚点 + 2 个版本标签笔误，均在 fix 切片），
已全部修复并重跑出榜。产物在 `../results/engmem-annotation/`（标注单、
三份裁决表、理由 JSONL、作者仲裁表），κ 计算见
`annotation_pack.py --kappa/--fleiss`。

## 标注流程与已知限制

- 题目由 AI 依据仓库真实文本预标、再逐条人工复核锚点与答案；**单标注者，
  无双人标注一致性（κ）**——这是与计划目标的偏差，论文中须如实声明。
- 题量：convention 144 / prohibition 78 / fix 93。禁令与修复类略低于计划
  区间下限（100-150），取舍是质量优先：每题必须锚定真实文件节，宁可少
  不可编。
- 题目与答案均为英文（仓库原文为英文）；评测的是检索命中，不涉及生成。
- 语料文本片段的再分发受各仓库许可证（MIT/Apache/BSD）允许；完整仓库
  不随本基准分发，`collect.py` 现场克隆。
