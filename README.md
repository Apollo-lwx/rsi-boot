# RSI Boot

个人本地 MCP 工具：**宿主模型（Cursor 等）的程序性记忆层**。
把交互中验证有效的经验、约定与禁止项沉淀为记忆，经审批后自动注入宿主规则文件
（`.cursor/rules/rsi-*.mdc` + `AGENTS.md` 托管块），并在任务开始前经 `rsi_recall` 召回——
下次同类任务自动规避已踩过的坑。**零 API Key**：主链路无模型调用，生成全部透传宿主模型；
数据落在当前工作目录的 `.rsi/`（和 Superpowers 的 `.superpowers/` 一样：一个工作区一份）。

## 安装

```bash
pip install -e .
```

## 快速上手

```bash
# 1. 初始化用户配置（~/.rsi/config.yaml）；项目记忆在工作目录 .rsi/
rsi init

# 2. 冒烟：记忆召回（零 Key，无需任何配置）
rsi recall "帮我审查这段 Python 代码"

# 3. 添加记忆并验证召回（FTS5 + CJK bigram）
rsi knowledge add --title "项目约定" --content "本项目统一使用 snake_case 命名"
rsi recall "本项目的命名约定是什么"

# 4. 项目自学习：扫描文档/配置/规范，无冲突原文直通生效；抽取与冲突留待确认
rsi bootstrap --dry-run          # 预览：信号发现 + 将直通 / 将进确认
rsi bootstrap --local-judge      # 正式学习（幂等，未改文件会跳过）
rsi bootstrap --local-judge --force   # 忽略 manifest 指纹，按当前文件全量再扫
rsi bootstrap --include .auto-learn   # 默认不扫 .worktrees/.auto-learn/.superpowers/任意 artifacts/，按需加回

# 5. 若工作区仍有旧版 rsi.db，先迁到文件记忆
rsi memory migrate

# 6. 启动 MCP stdio server（接入 Cursor 等客户端）
rsi serve
```

在 Cursor 对话里由 agent 重新学习时走 `--host-judge`（冲突工作包由宿主模型当场裁决）；你自己敲终端用 `--local-judge`（本地整条比对，宁缺毋滥）。两者必须且只能给一个，`--dry-run` 除外。

无冲突的仓库原文直接生效。抽取与冲突在 Cursor 对话里由 agent 调 MCP 工具确认，无需在终端手动跑 `rsi knowledge accept` 等命令。
约定（convention）获准后只进召回，**不会**写成 `rsi-convention-*.mdc` 规则文件。

**重新学习：** `--force` 只忽略指纹，**不会**把旧版溢出归档（无 `bootstrap_run_id`）救回 `active`。曾用旧逻辑学过、记忆几乎全是归档时，先清文件记忆再学（删除 `.rsi` 下 `memory/` `logs/` `cache/` `audit/` `state/` 与 `manifest.json`，残留 `rsi.db*` 也会删；`identity.json` 会保留）：

```bash
# 在该项目根目录。若 Cursor 会立刻把 MCP 拉回来，先在 Settings → MCP 关掉 rsi-boot
rsi wipe --yes
rsi bootstrap --local-judge
```

## MCP 客户端配置（Cursor 示例）

```json
{
  "mcpServers": {
    "rsi-boot": {
      "command": "rsi",
      "args": ["serve"],
      "env": {
        "RSI_PROJECT_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

`rsi` 可以全局安装。Cursor 用户级 `mcp.json` 里 **`args` 中的 `${workspaceFolder}` 经常不展开**（会落到字面路径，记忆串库）。工作区请走环境变量 `RSI_PROJECT_ROOT`，或依赖 Cursor 注入的 `WORKSPACE_FOLDER_PATHS` / `CURSOR_WORKSPACE_ROOT`。未展开的 `${…}` 会被忽略并回落这些变量。每个窗口仍会在**该仓库**下创建自己的 `.rsi/`，和 Superpowers 的 `.superpowers/` 一样。

暴露工具（v3.0）：

| 工具 | 说明 |
|------|------|
| `rsi_recall` | 记忆召回入口：任务开始前调用，禁止项置顶 + 相关经验（召回臂 Thompson 调参） |
| `rsi_feedback` | 采纳反馈（accepted/applied/modified/copied/referenced/ignored/rejected + 评分/评语）；rejected+评语将提炼为禁止项 |
| `rsi_knowledge_add` / `rsi_knowledge_search` / `rsi_knowledge_delete` / `rsi_knowledge_review` | 记忆管理与提取草稿审批；add/review/delete 触发规则文件重写 |
| `rsi_review` | Harness 改进提案（模板化生成 + 静态门禁）与配置快照 |
| `rsi_conflicts` | 学习记忆与你手写规则的冲突查询与裁决（user_wins/memory_wins/coexist） |
| `rsi_stats` | 记忆使用统计：触达/采纳率/禁止项遵循率/提案通过率（JSON/CSV 导出） |

> v3.0 起 `rsi_query` 退役：RSI Boot 不再自己调用 LLM 生成答案，专注做记忆层。
> 原模型调用链（Pipeline/质量评判/LLM 提取等）归档为附录 C 可选增强，默认关闭。

## 配置链

包内 `default.yaml` → `~/.rsi/config.yaml` → 项目根 `rsi-boot.yaml` → 请求参数（受保护路径除外）。
`RSI_HOME` 只覆盖**用户配置**目录（默认 `~/.rsi`：`config.yaml`、可选 skills）。项目记忆、画像、归档都在工作目录 `.rsi/`（`memory/` / `identity.json` / `state/`），打开另一个仓库就是另一份，不会串。若 MCP 不是从仓库根启动，设环境变量 `RSI_PROJECT_ROOT`，或把 server 配在项目 `.cursor/mcp.json` 里以保证 cwd 为工作区。旧版 `.rsi/rsi.db` 先跑 `rsi memory migrate` 再 `rsi serve`。`rsi serve` 运行期间配置文件变更自动热加载（last-good-wins）。

主链路零配置可用。可选增强（`enhance.*`，需自配模型 Key）见 `~/.rsi/config.yaml` 示例：
`enhance.embedding`（向量检索）、`enhance.extract_llm`（LLM 知识提取）、`enhance.proposal_llm`、
`enhance.gate_replay`（回放回归门禁）、`enhance.intent_llm`、`enhance.conflict_llm`。
环境变量白名单：`RSI_OPENAI_API_KEY` / `OPENAI_API_KEY`、`RSI_OPENAI_BASE_URL` / `OPENAI_BASE_URL`（仅增强层使用）。

安全要点：项目 `rsi-boot.yaml` 禁止明文密钥（发现即拒绝启动）；`~/.rsi/config.yaml` 中可用 `env:VAR_NAME` 引用环境变量；
日志落库前执行十条规则脱敏；注入宿主上下文前过三道闸（脱敏 + 审批闸门 + 注入黑名单）；
超 90 天日志自动归档到项目 `.rsi/archive/YYYYMM.jsonl` 后删除。

## 角色支持

请求携带 `role` 字段启用角色意图（`test` / `pm` 内置）：角色意图优先匹配，未命中走通用意图兜底。
新角色通过 `config/roles/<role>.yaml` + `config/role_templates/` 纯配置扩展，无需改代码。

## 开发

```bash
pip install -e ".[dev]"
python -m pytest
python scripts/export_schemas.py   # 由 Pydantic 模型重新生成 schemas/*.json
```

设计文档见 `docs/`（PRD / Spec / Plans；v3.x 为当前权威，v2.6 已冻结存档）。
