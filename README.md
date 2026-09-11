# RSI Boot

个人本地 MCP 工具：**宿主模型（Cursor 等）的程序性记忆层**。
把交互中验证有效的经验、约定与禁止项沉淀为记忆，经审批后自动注入宿主规则文件
（`.cursor/rules/rsi-*.mdc` + `AGENTS.md` 托管块），并在任务开始前经 `rsi_recall` 召回——
下次同类任务自动规避已踩过的坑。**零 API Key**：主链路无模型调用，生成全部透传宿主模型；
数据全部落在本地 SQLite（`~/.rsi/rsi.db`）。

## 安装

```bash
pip install -e .
```

## 快速上手

```bash
# 1. 初始化（创建 ~/.rsi/ 与数据库，执行迁移）
rsi init

# 2. 冒烟：记忆召回（零 Key，无需任何配置）
rsi recall "帮我审查这段 Python 代码"

# 3. 添加记忆并验证召回（FTS5 + CJK bigram）
rsi knowledge add --title "项目约定" --content "本项目统一使用 snake_case 命名"
rsi recall "本项目的命名约定是什么"

# 4. 项目自学习：扫描文档/配置/规范，产出记忆草稿（入审批队列，确认后生效并注入）
rsi bootstrap --dry-run          # 预览扫描计划
rsi bootstrap                    # 正式学习（幂等，可重复执行）

# 5. 启动 MCP stdio server（接入 Cursor 等客户端）
rsi serve
```

## MCP 客户端配置（Cursor 示例）

```json
{
  "mcpServers": {
    "rsi-boot": {
      "command": "rsi",
      "args": ["serve"]
    }
  }
}
```

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
`RSI_HOME` 可覆盖数据目录（默认 `~/.rsi`）。`rsi serve` 运行期间配置文件变更自动热加载（last-good-wins）。

主链路零配置可用。可选增强（`enhance.*`，需自配模型 Key）见 `~/.rsi/config.yaml` 示例：
`enhance.embedding`（向量检索）、`enhance.extract_llm`（LLM 知识提取）、`enhance.proposal_llm`、
`enhance.gate_replay`（回放回归门禁）、`enhance.intent_llm`、`enhance.conflict_llm`。
环境变量白名单：`RSI_OPENAI_API_KEY` / `OPENAI_API_KEY`、`RSI_OPENAI_BASE_URL` / `OPENAI_BASE_URL`（仅增强层使用）。

安全要点：项目 `rsi-boot.yaml` 禁止明文密钥（发现即拒绝启动）；`~/.rsi/config.yaml` 中可用 `env:VAR_NAME` 引用环境变量；
日志落库前执行十条规则脱敏；注入宿主上下文前过三道闸（脱敏 + 审批闸门 + 注入黑名单）；
超 90 天日志自动归档到 `~/.rsi/archive/YYYYMM.jsonl` 后删除。

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
