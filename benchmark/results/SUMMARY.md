# RepoMem Bench · 首轮试点结果（2026-10-23/24）

> 状态：试点（pipeline 校准用），**不是**可发布的结论。样本 3 任务 × 3 组 × 1 次，被测 agent 单一。

## 跑批设置

- 被测 agent：Cursor 子代理（generalPurpose，继承模型），每跑一次全新实例（无补丁污染——只见 prompt 与浅克隆工作目录）
- 编排：runner 两阶段（`--prepare-only` / `--collect-only`），收集时注入隐藏测试补丁
- 组别：A 裸 prompt / B 仓库根 AGENTS.md / C `rsi bootstrap` + 批准后的记忆召回（CLI 路径，忠实度说明见下）
- 任务：packaging-prerelease-gt-lt、jinja-int-filter-scientific（Python）、zod-email-single-quote（TS），全部 SWE-bench 式真实 issue 构造

## 结果

| 任务 | A（裸） | B（AGENTS.md） | C（RSI 记忆） |
|------|---------|----------------|----------------|
| packaging-prerelease-gt-lt | PASS · 38.9min | PASS · 18.9min | PASS · 计时失真* |
| jinja-int-filter-scientific | PASS · 38.9min | PASS · 18.8min | PASS · 计时失真* |
| zod-email-single-quote | PASS · 39.5min | PASS · 19.2min | PASS · 计时失真* |
| 约定违反 | 0 | 0 | 0 |

\* C 组代理凌晨完成、次晨才收集，时长字段为「准备→收集」墙钟，不代表 agent 工作时长。下轮修复：时长以 agent 自报或后端时间戳为准。

## 发现与方法论教训（本轮最有价值的产出）

1. **任务区分度不足**：3 个任务对现代 agent 都太简单，A 组裸跑也全过——pass rate 无法区分三组。下轮需要「A 组会失败」的更难任务（跨文件修复、隐晦约定、大型仓库）。
2. **静态上下文的时间价值是实的**：B 组比 A 组快约 2 倍（18.9 vs 38.9 分钟，同任务同模型）——光是一份 AGENTS.md 就省一半探索时间。这是可放大的叙事点。
3. **提示词自残**：本轮 prompt 直接把禁止路径（tests/、deno/ 等）写给了所有组，导致「约定违反」指标三组全 0、失去区分度。**下轮 prompt 只给任务本身；约定只能从 B 的 AGENTS.md / C 的记忆里获得**——这才是对上下文价值的真实检验。
4. **产品级发现（dogfooding）**：CLI `rsi knowledge add` 的条目停在 `pending_review`，CLI 无批准路径（`knowledge accept` 只管 bootstrap 抽取），最终走 MCP `rsi_knowledge_review` 批准才进召回；且任务级中文查询召回偏弱（已按规则上报 4 条 feedback：1 referenced + 3 rejected）。建议产品补：`rsi knowledge accept` 覆盖手动条目；recall 空结果时提示存在待审条目。
5. **C 组忠实度**：本轮 C 组记忆为人工压缩蒸馏（4 条/库，contributor 文档级），非完整 168 包流程；agent 自行 `rsi recall` 并成功命中约定与禁止项（zod 组命中 deno 禁止项并遵守）。

## 原始数据

同目录 `2026102*-{task}-{arm}.json`（含 resolved SHA、命令、退出码、diff 清单；token 均为 null——manual 后端不产 token 数据，不估算）。
