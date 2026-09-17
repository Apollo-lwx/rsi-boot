# RSI Boot

简体中文 | [English](README.en.md)

宿主模型（Cursor 等）的本地记忆层。把对话里验证过的约定、禁止项和修复经验沉淀下来，经审批后注入规则文件，并在下次任务前召回。零 API Key，主链路不调用模型；数据只落在当前仓库的 `.rsi/`。

## 安装

需要 Python 3.10+。在本仓库根目录：

```bash
pip install -e .
rsi init
```

`rsi init` 只写用户配置 `~/.rsi/config.yaml`。项目记忆在各仓库的 `.rsi/`，第一次启动 MCP 或 bootstrap 时创建。

## 接到 Cursor

在**要用记忆的那个项目**的 `.cursor/mcp.json` 加：

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

然后 Settings → MCP 确认 `rsi-boot` 已启用。改过本仓库代码后要重载该 MCP。

不要配用户级 `mcp.json`：那里 `args` 里的 `${workspaceFolder}` 经常不展开，会把记忆串到别的仓库。工作区绑定顺序：`RSI_PROJECT_ROOT`（项目级 mcp.json，推荐，显式契约）→ MCP `roots/list`（插件形态下由 Cursor 自动应答）→ Cursor 注入的 `WORKSPACE_FOLDER_PATHS` → 进程 cwd。

## 在对话里学习

接好 MCP 后不要自己去终端跑 `rsi`。对当前项目窗口里的代理说即可。

| 你说 | 代理做的 |
|------|----------|
| 第一次学这个项目 | `rsi bootstrap --consent --host-judge` → `rsi_learn` 蒸完全部 pending → `rsi_knowledge_review` 批准本 run |
| 重新学习 | 先 `rsi wipe --yes` 清记忆，再按上面重新学 |

看到「必须指定 `--host-judge` 或 `--local-judge`」就加 `--host-judge` 重跑，不要改用 `--local-judge`（那条只给终端自己采集用）。`rsi wipe --yes` 会删掉该项目 `.rsi` 下的 `memory/` `logs/` `cache/` `audit/` `state/` 与 `manifest.json`，保留 `identity.json`；若提示文件被占用，先在 Settings → MCP 关掉 `rsi-boot` 再重跑。

学完看 `.rsi/bootstrap_report.md` 的板块清单，pending=0 并经 `rsi_knowledge_review` 批准后才算召回可用。约定获准后只进召回，不会写成 `rsi-convention-*.mdc` 规则文件。

## 终端冒烟

不经对话、只调 CLI 时用。写盘必须带 `--host-judge` 或 `--local-judge` 其中一个；`--dry-run` 除外。

```bash
rsi recall "帮我审查这段 Python 代码"
rsi knowledge add --title "项目约定" --content "本项目统一使用 snake_case 命名"
rsi recall "本项目的命名约定是什么"

rsi bootstrap --dry-run
rsi bootstrap --local-judge
rsi bootstrap --local-judge --force

# 旧版 .rsi/rsi.db 先迁移
rsi memory migrate

# 一般由 Cursor 拉起，不必手跑
rsi serve
```

## MCP 工具

| 工具 | 干什么 |
|------|--------|
| `rsi_recall` | 任务开始前召回：禁止项置顶 + 约定/文档/gene/teaching |
| `rsi_feedback` | 采纳反馈；rejected+评语会提炼为禁止项 |
| `rsi_learn` | 阅读包 pack_list / pack_open / pack_done，teach_catch / teach_record，Session 审计 |
| `rsi_knowledge_add` / `rsi_knowledge_search` / `rsi_knowledge_delete` / `rsi_knowledge_review` | 记忆管理与审批；蒸馏条须带 `pack_id` |
| `rsi_conflicts` | 短知识冲突查询与裁决 |
| `rsi_memory` | 记忆索引 / 打开 / 重建 / 关系图 |
| `rsi_review` | Harness 改进提案与配置快照 |
| `rsi_stats` | 记忆使用统计 |

RSI 无 OAuth。蒸馏中途不要调 `mcp_auth`、不要给用户弹授权。

## 配置

配置链：包内 `default.yaml` → `~/.rsi/config.yaml` → 项目根 `rsi-boot.yaml` → 请求参数。

`RSI_HOME` 只覆盖用户配置目录（默认 `~/.rsi`）。项目记忆、画像、归档都在工作目录 `.rsi/`，打开另一个仓库就是另一份，不会串。主链路零配置可用；可选增强（向量检索、LLM 提取等，需自配模型 Key）见 `~/.rsi/config.yaml` 示例。

项目 `rsi-boot.yaml` 禁止明文密钥，发现即拒绝启动。日志落库前脱敏；注入宿主上下文前过审批闸门与黑名单；超 90 天日志自动归档到 `.rsi/archive/YYYYMM.jsonl`。

## 开发

```bash
pip install -e ".[dev]"
python -m pytest
python scripts/export_schemas.py
```
