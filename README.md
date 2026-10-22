<div align="center">
  <img src="assets/logo.svg" alt="rsi-boot logo" width="120">
</div>

# RSI Boot

简体中文 | [English](README.en.md)

Agent 记忆很多人都在做。RSI Boot 要解决的是另外两件事：**harness 会跟着用、跟着改**，以及**记忆跟着仓库走、换宿主不重学**。

主链路零 API Key，不调用模型。约定、禁止项、修复经验和 harness 槽位都只落在当前仓库的 `.rsi/`，下次任务前按需召回。

## 和「又一个记忆插件」差在哪

**rsi-harness（Harness-RSI）**
负反馈进失败桶，只改可写槽位（提示词 / 技能 / 召回策略 / 意图规则 / 知识）。提案过静态门禁和人工确认才晋升，观察 7 天，采纳率掉就回滚。越用越会干活，不是越记越多。

**跟随项目迁移**
记忆按仓库隔离，不跟账号、不跟 IDE。仓库拷走、换机器、换目录，`.rsi/` 一起走；打开另一个仓库就是另一份，不会串。Harness 基因组可导出 bundle，跨项目备份或迁槽位。

**多平台兼容**
同一套 MCP（`rsi serve`）接 Cursor、Qoder、Codex；状态栏同时吃 Claude Code 与 Cursor CLI。规则注入走 `.cursor/rules` 和各宿主都认的 `AGENTS.md` 托管块，换工具不用把项目史再讲一遍。

| | 宿主自带记忆 | 云端记忆（Mem0 一类） | 通用本地记忆 MCP | RSI Boot |
|---|---|---|---|---|
| 数据跟谁走 | 跟账号 / 跟宿主 | 跟云端用户 | 看实现 | **跟当前仓库** |
| 换 Cursor / Claude Code / Codex | 重学 | 对方接得上才有 | 多数只做存取 | **同一份 `.rsi/`** |
| Harness 自改进 | 无 | 无 | 无或弱 | **提案 → 门禁 → 观察 → 回滚** |
| 主链路 API Key | 绑宿主 | 要 | 常要 | **不要** |
| 写入闸门 | 模型自觉 | 服务端策略 | 少 | **审批 + 禁止项 + 冲突裁决** |

## 安装

需要 Python 3.10+：

```bash
pip install rsi-boot
rsi init
```

要改源码则克隆本仓库，按[开发](#开发)一节用可编辑安装。

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

## 接到其他宿主

### Qoder

本仓库同时是 Qoder 插件：`.qoder-plugin/plugin.json` 清单 + 根目录 `mcp.json`（与 Cursor 插件共用同一份 MCP 配置），打 zip 即可分发。手动接的话二选一：

```bash
qoder mcp add rsi-boot -- rsi serve
```

或在项目根 `.mcp.json` 加与 Cursor 相同的 `mcpServers` 片段。工作区绑定顺序与 Cursor 一致（`RSI_PROJECT_ROOT` → roots/list → 注入变量 → cwd）。

### Codex

Codex 没有插件包格式，MCP 走 TOML 配置：用户级 `~/.codex/config.toml`，或项目级 `.codex/config.toml`（需目录已被信任）：

```toml
[mcp_servers.rsi-boot]
command = "rsi"
args = ["serve"]
```

从项目根目录启动 codex 即可——MCP 子进程继承 cwd，rsi-boot 按 cwd 向上认领工作区。要显式钉死就在表里加一行：`env = { RSI_PROJECT_ROOT = "/绝对/路径" }`。

## 状态栏（可选）

`rsi statusline` 给宿主 CLI 渲染底部状态栏：模型、上下文余量、token 统计（in/out/cache）、花费、RSI 今日召回与采纳率，外加一条定期轮换的随机文案。Claude Code 与 Cursor CLI 通用（两家的 statusLine 都会把会话 JSON 从 stdin 喂进来，本命令解析后单行输出，带 ANSI 颜色与 emoji）。

Claude Code（`~/.claude/settings.json`）：

```json
{
  "statusLine": { "type": "command", "command": "rsi statusline" }
}
```

Cursor CLI（`~/.cursor/cli-config.json`）：

```json
{
  "statusLine": { "type": "command", "command": "rsi statusline" }
}
```

效果示例：

```
🧠 Kimi K3 │ 📊 上下文余 71% │ ⛽ in 12.3k · out 1.8k · cache 96k │ 💰 $0.42 │ 🐾 RSI 召回 3 · 采纳 92% │ 给你比个心❤
```

### 选文案风格

三种配法并列，按场景任选（优先级：`--style` > 环境变量 > 项目级 > 用户级）：

```jsonc
// ① 跟着宿主走：直接写进 statusLine 的 command（最直观，不动任何 config.yaml）
{ "statusLine": { "type": "command", "command": "rsi statusline --style buddy" } }
```

```yaml
# ② 项目级（按仓库定风格，跨宿主共享）：<工作区>/.rsi/config.yaml
# ③ 用户级（全局默认）：~/.rsi/config.yaml
statusline_style: buddy
```

六档预设：`mixed` 混搭（默认）、`buddy` 陪伴搭子（WorkBuddy 风：陪伴/安抚/治愈/祝福）、`serious` 严肃专业（只出通用陪伴与金句，不见猫咪玄学）、`healing` 治愈可爱、`geek` 极客梗、`lucky` 玄学好运。改完即生效，无需重启——状态栏每次刷新都会重读。语言键 `lang` 在 config.yaml 里走同一条优先级链。

注意：状态栏不是 MCP server，不由 mcp.json 管理（那是 `rsi serve` 记忆服务的配置），所以风格只能配在 statusLine 的 command 或 config.yaml 里。

### 说明：

- token 明细来自 transcript（Claude Code）或 `context_window` 字段（Cursor CLI）；上下文窗口默认 200k，`exceeds_200k_tokens` 时按 1M，可用 `RSI_STATUSLINE_CTX_WINDOW` 覆盖。
- 文案三层供给：**用户文案文件**（`~/.rsi/quips.json`，板块非空即整体替换内置池）> 手写池（130+ 条，质量下限）+ 模板造句器（约 200 种组合，按组合空间加权）。用户文案由宿主模型在对话中生成（对它说「生成状态栏文案」即可，风格规范内嵌在文件头），写完跑 `rsi statusline --validate-quips` 校验口径与去重。
- 风格按主题（core/motto/kudos/fortune/geek/dry/healing/food/cat/game/hype/slack/offwork/weather）打标过滤；场景池（早安/深夜/告急/久坐/夸夸）功能性文案不受风格限制。
- 轮换与防重：默认 45 秒一换（`RSI_STATUSLINE_QUIP_SECONDS` 可调，如 5），同桶内不闪烁；`~/.rsi/state/statusline_quips.json` 记录每会话最近 300 条已播，自动避开重复。
- 场景专属池：早安（06–11 点）、深夜（23–06 点）、上下文告急（≤15%）、久坐提醒（会话 ≥45 分钟，`RSI_STATUSLINE_REST_MINUTES` 可调，0 关闭）、RSI 高采纳夸夸。
- RSI 段从 cwd 向上找 `.rsi/logs/events.jsonl` 统计今日数据，找不到就静默省略。
- 语言只显示一种，解析链：`--lang` → `RSI_LANG` → 项目 `.rsi/config.yaml` → 用户 `~/.rsi/config.yaml` → 系统语言。想固定就写一行 `lang: zh`（项目级优先）。
- 选项：`--lang zh|en`、`--no-rsi`、`--no-quip`、`--plain`（去颜色与 emoji）；`NO_COLOR` 只关颜色。
- 冷启动约 250ms，满足宿主 ≥300ms 刷新间隔；命令永不非零退出，异常时兜底输出一行。

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
| `rsi_explore` | 按 domain 板块下钻浏览知识树（空 path 列顶层板块+计数，传 path 列成员条目标题/简述/id，并附本节点条目的图谱关联 related 与冲突 conflicts） |
| `rsi_feedback` | 采纳反馈；rejected+评语会提炼为禁止项 |
| `rsi_learn` | 阅读包 pack_list / pack_open / pack_done，teach_catch / teach_record，Session 审计 |
| `rsi_knowledge_add` / `rsi_knowledge_search` / `rsi_knowledge_delete` / `rsi_knowledge_update` / `rsi_knowledge_review` | 记忆管理与审批；`rsi_knowledge_update` 支持原地改字段（改优于新建重复）与 `merge` 合并近重复（保留更完整者、其余归档并写 supersedes 边）；蒸馏条须带 `pack_id` |
| `rsi_conflicts` | 短知识冲突查询与裁决 |
| `rsi_memory_health` | 错误/过时记忆发现与裁决：评语极性打脸开 `incorrect`，来源失效开 `stale_source`；`resolve` 才能归档，扫描本身不动正式库 |
| `rsi_memory` | 记忆索引 / 打开 / 重建 / 关系图 |
| `rsi_review` | Harness 改进提案与配置快照 |
| `rsi_stats` | 记忆使用统计 |
| `rsi_impact` | 价值/影响力面板（供宣讲）：注入成本（字符+token 估算）、召回压缩比、采纳/合规、时间曲线，以及 A/B 省 token 计算器（省量仅由用户喂入的宿主用前/用后对照得出，绝不编造） |

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
