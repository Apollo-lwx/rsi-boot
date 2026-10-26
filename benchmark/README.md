# RepoMem Bench

> repo 绑定 agent 记忆的公开评测基准 · 状态：v0 草稿（任务集验证中，未发布任何结果）
> 维护：RSI Boot 项目 · 欢迎第三方复跑与提交任务

## 为什么要有这个基准

LOCOMO 一类基准量的是「对话记忆」——聊天事实的抽取与召回。它不适用于编码场景：没有仓库、没有测试套件、也没有「违反仓库约定」这回事。

RepoMem Bench 量的是另一件事：**一个 agent 进入真实仓库时，记忆/上下文方案能不能让它一次就把活干对、干合规矩。**

## 三组对照（arms）

| 组 | 上下文方案 | 说明 |
|----|-----------|------|
| A | 无任何额外上下文 | 基线 |
| B | 仓库根注入人工编写的 AGENTS.md | 静态最佳实践的上限 |
| C | 先跑 `rsi bootstrap` 再开工 | RSI Boot |

同一任务、同一模型、同一温度、同一超时，三组各跑一遍。跑批时模型版本与参数必须写入结果 JSON。

## 指标

- **一次通过率（主指标）**：验证命令首次运行退出码为 0 的比例。
- **token 消耗**：agent 后端报告的 input/output token。**取不到就记 null，绝不估算编造。**
- **约定违反数**：diff 触碰任务声明的 `forbidden_paths` 次数（机械可判，不依赖主观评审）。
- **辅助指标**：耗时、改动文件数。

## 任务 schema（repomem-task/v1）

见 [`tasks/examples/`](tasks/examples/) 两个带注释的示例。关键字段：

| 字段 | 含义 |
|------|------|
| `id` / `status` | 任务标识；`draft` → `validated`（通过 `--validate` 才进得分集） |
| `repo.url` / `repo.ref` | 仓库与 ref（tag/branch）；runner 记录 resolved SHA 到结果 |
| `prompt` | 交给 agent 的任务描述，唯一输入 |
| `setup` / `verify` | 环境准备与验证命令；验证退出码即通过与否 |
| `forbidden_paths` | 约定违反的机械判定：diff 触碰即计一次违反 |
| `agents_md` | B 组注入的静态上下文（A 组不注入、C 组不用——C 组用 bootstrap 产物） |
| `isolate` | `venv` 时 setup/verify 在仓库内独立 venv 执行，不污染调用者环境 |
| `verify_patch` | 判分测试补丁（相对任务文件的路径），验证时才注入，agent 不可见 |
| `gold_patch` | 参考修复补丁，仅供 `--validate` 证明任务可解 |

**issue 级任务（SWE-bench 式）**：base 钉在真实修复 commit 的父提交，`verify_patch` 是该 commit 的测试部分、`gold_patch` 是源码部分。`--validate` 两步法：① 仅测试补丁在未修复代码上必须 FAIL（区分度）；② gold+测试补丁必须 PASS（可解性）。smoke 级任务（无补丁）只要求 pristine verify 通过。

> 补丁导出务必用 `cmd /c "git diff A B -- paths > file.patch"` 原生重定向——PowerShell `>` 会把输出转成字符串往返，破坏补丁格式（git apply 报 "No valid patches"）。

## 仓库选择标准

- 开源、测试套件本地可确定性运行、无外部服务依赖、无密钥要求
- 覆盖至少 3 种语言栈（首批目标：Python / TypeScript / Go）
- 任务来自真实 issue/PR 或可机械判定的工程需求，拒绝玩具题

## 运行

```bash
# 校验任务：克隆 + 跑 setup + 在 pristine 仓库上执行 verify（draft → validated 的前提）
python benchmark/run_benchmark.py --task benchmark/tasks/examples/markupsafe-docstring-example.yaml --validate

# 跑一个任务（manual 后端：打印 prompt，由人/agent 在外部完成后回车收集结果）
python benchmark/run_benchmark.py --task benchmark/tasks/examples/markupsafe-docstring-example.yaml --arm A --backend manual
```

克隆产物落在 `benchmark/.workdir/`（gitignore，不入库）；结果写 `benchmark/results/<run_id>.json`（原始入库）。

## 诚实条款

1. 结果 JSON 为原始记录：resolved commit SHA、命令、退出码、耗时，不接受任何「估算」数字。
2. 任务必须先通过 `--validate` 才计入得分集。
3. 任何人可用同一任务集复跑；发现结果不可复现，请开 issue。
4. 本基准由 RSI Boot 项目维护且 C 组即本项目——利益相关明示于此；欢迎社区接管治理。

## 路线图

- [x] 方法 v0 / 任务 schema / runner 骨架（manual 后端 + `--validate` 可用）
- [x] setup 隔离：`isolate: venv` 任务在仓库内独立 venv 执行 setup/verify
- [x] claude 后端：`claude -p --output-format json` headless，采集真实 token/轮次/花费；C 组自动写 `.mcp.json`（已实现，待本机装 claude CLI 后校准首跑）
- [x] 已验证任务 ×3：markupsafe 3.0.2 / itsdangerous 2.2.0 / packaging 24.2（均为 smoke 级；pflag 待 Go 工具链）
- [x] runner 加固：残次克隆（缺 ref）自动重克隆；半成品 venv（缺 python 可执行文件）自动重建
- [x] SWE-bench 式 issue 级任务 ×6 已验证：packaging×2、jinja×3（Python）、zod×1（TypeScript）；另有 smoke 级 ×3
- [x] 克隆提速：SHA 钉死的任务走浅取单提交（`git fetch --depth 1 origin <sha>`），大仓库/弱网友好
- [ ] 首批 20 个任务（≥3 语言栈）验证入库——当前 15/20（含「违规诱惑型」×4：packaging-prerelease、packaging-filename-no-raise、jinja-empty-required-block、mi-windowed-invalid-size——base 自带测试断言旧行为，修对后旧断言必失败，专测约定约束力）；继续按 SWE-bench 式构造，补 Go 栈
- [x] 「违规诱惑型」任务集立项：扫描器按「改实现+测试含删行（断言翻转）」启发式批量挖 commit，已验证 3 个新任务入库
- [x] runner 两阶段（`--prepare-only` / `--collect-only`）：任意编排者可驱动；`mcp_call.py` 走通 CLI 缺失的批准/反馈流
- [x] runner 加固 v2：收集时先还原补丁目标文件再注入（防改测试糊分弄崩打分，违规照记）；`forbidden_paths` 目录前缀匹配修复（`tests/` 此前因 fnmatch 无语义而失效——首轮全部「0 违反」系该 bug 所致，已对受影响的 parse-decimal [A] 结果人工更正）
- [x] **首轮试点完成**（3 任务 × 3 组 × Cursor 子代理，9/9 PASS）：见 [results/SUMMARY.md](results/SUMMARY.md)——产出 5 条方法论教训（最重要：任务需加难度、prompt 不得泄露约定）
- [x] **v2 完成**（3 硬任务 × 3 组）：见 [results/SUMMARY-v2.md](results/SUMMARY-v2.md)——**首次量化约定约束力**（A 组改测试违规 1 起 vs B/C 全守规矩）；发现过规格化测试陷阱；C 组代理自发完成 teach_record+feedback 闭环
- [x] **批 1 完成**（4 任务 × 3 组 = 12 跑，36 跑计划首批）：见 [results/SUMMARY-v3-batch1.md](results/SUMMARY-v3-batch1.md)——**诱惑任务上 A 违规 2/2、B/C 0/8；累计 A 4/5 vs B 0/6 vs C 1/6**；`seed_memory.ps1` 一键播种 C 组记忆
- [x] **批 2 完成**（第二轮重复 12 跑）：见 [results/SUMMARY-v4-batch2.md](results/SUMMARY-v4-batch2.md)——**可重复性确立：A 在真诱惑任务累计 6/7 违规 vs B 0/7 vs C 1/8**；非诱惑任务三组全 0（交叉验证违规由诱惑诱发）
- [x] **36 跑计划完成（批 1–3）**，并经第 4 批复跑更新为 48 跑：见 [results/SUMMARY-v5-final-36runs.md](results/SUMMARY-v5-final-36runs.md)——**诱惑任务上 A 违规 67%（8/12）vs B 0%（0/12）vs C 8%（1/12）**（第 4 批前为 78% / 0% / 11%；同日误记的 17% 已撤回），含方法、效度威胁与复跑指引
- [ ] 下轮：B/C 分化任务设计（多文件/长程/token 维度）；任务入库 checklist 增加「人工审查精确文本断言」
- [ ] cursor / codex 后端
- [ ] 正式首轮结果 + 排行榜
