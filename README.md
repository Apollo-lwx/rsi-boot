<div align="center">
  <img src="assets/logo.svg" alt="rsi-boot logo" width="120">

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=rsi-boot&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJyc2ktYm9vdCIsInJzaSIsInNlcnZlIl19)
[![Install in VS Code](https://img.shields.io/badge/Install%20in-VS%20Code-0078d7?logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%22rsi-boot%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22rsi-boot%22%2C%22rsi%22%2C%22serve%22%5D%7D)
[![rsi-boot MCP server](https://glama.ai/mcp/servers/Apollo-lwx/rsi-boot/badges/score.svg)](https://glama.ai/mcp/servers/Apollo-lwx/rsi-boot)
</div>

<!-- mcp-name: io.github.Apollo-lwx/rsi-boot -->

# RSI Boot

简体中文 | [English](README.en.md)

> **Agent 的工程记忆，不是对话记忆：约定和禁止项落在仓库里，换一个宿主读到的还是这一份。**

Cursor 在 2.1 删掉了 Memories，新会话对你的项目一无所知；Claude Code 的记忆锁在本机、不跟 git，换一个同事就对不上；云记忆要 Key、要服务，记忆还跟账号走。同一个人换着 Cursor、Claude Code、Codex、DeepSeek Harness 写同一个仓库，或者团队里各用各的工具，写法、禁止项和修 bug 的经验也各记各的。

RSI Boot 把约定、禁止项、修复经验和 harness 槽位落在仓库的 `.rsi/`，跟仓库走：装一次，Cursor / Claude Code / Codex / Qoder / DeepSeek Harness 通用。主链路零 API Key，不调用模型。

环是固定的：**召回 → 干活 → 蒸馏成待审条目 → 人批准后才留下 → 下一轮召回读到留下的条目。** 负反馈另外进失败桶，只改可写槽位（提示词 / 技能 / 召回策略 / 意图规则 / 知识）。提案过静态门禁和人工确认才晋升，观察 7 天，采纳率掉就回滚。这是软件自己的写法。公开结果还没有和别人的答题分放在同一列。召回主榜的表在 [benchmark/results/recall-20261028/SUMMARY.md](benchmark/results/recall-20261028/SUMMARY.md)。

## 和「又一个记忆插件」差在哪

**rsi-harness（Harness-RSI）**
负反馈进失败桶，只改可写槽位（提示词 / 技能 / 召回策略 / 意图规则 / 知识）。提案过静态门禁和人工确认才晋升，观察 7 天，采纳率掉就回滚。留下的是槽位版本。这条环已经在软件里；公开基准还没有量过「下一轮是否更好」。

**跟随项目迁移**
记忆按仓库隔离，不跟账号、不跟 IDE。仓库拷走、换机器、换目录，`.rsi/` 一起走；打开另一个仓库就是另一份，不会串。Harness 基因组可导出 bundle，跨项目备份或迁槽位。

**跨 AI 工具、多人共用一份项目记忆**
同一套 MCP（`rsi serve`）接 Cursor、Qoder、Codex 和 DeepSeek Harness；状态栏同时吃 Claude Code 与 Cursor CLI。要共用的是这个仓库的项目记忆：函数名怎么写、支付日志里不能打卡号、上次空指针是怎么修的。聊天记录不在这里面。

核准后的约定写进仓库根的 `AGENTS.md` 托管块，也写进 `.cursor/rules`。Cursor、Claude Code、Codex、Qoder、DeepSeek Harness 打开同一份仓库，都会读到这段托管块。接上 MCP 之后，召回读的是同一份 `.rsi/`：上午在 Cursor 里记下的禁止项，晚上换 DeepSeek Harness、或者同事换 Codex 打开这个仓库，读到的是同一条。

同事要拿到召回用的全文，仓库里得有同一份 `.rsi/`。学习默认把 `.rsi/` 写进 `.gitignore`，日志和缓存留在本机。交给同事时，把核准过的 `.rsi/memory` 纳入版本库；Git 一旦忽略了整个 `.rsi/` 目录，里面的文件需要改忽略规则才能再提交。`AGENTS.md` 在仓库根，提交后大家拉下来就能读到托管块。

| | 宿主自带记忆 | 云端记忆（Mem0 一类） | 通用本地记忆 MCP | RSI Boot |
|---|---|---|---|---|
| 数据跟谁走 | 跟账号 / 跟宿主 | 跟云端用户 | 看实现 | **跟当前仓库** |
| 换 AI 工具、换同事 | 重学，各记各的 | 对方接得上才有 | 多数只做存取 | **同一份 `.rsi/` 与 `AGENTS.md`** |
| Harness 自改进 | 无 | 无 | 无或弱 | **提案 → 门禁 → 观察 → 回滚** |
| 主链路 API Key | 绑宿主 | 要 | 常要 | **不要** |
| 写入闸门 | 模型自觉 | 服务端策略 | 少 | **审批 + 禁止项 + 冲突裁决** |

## 公开基准

主榜是召回。冷启动默认 5 条。分母是有标准证据的题数。MRR 是第一条命中的名次倒数（0 到 1）。token 记 null。协议和分项在 [benchmark/results/recall-20261028/SUMMARY.md](benchmark/results/recall-20261028/SUMMARY.md)。

### 这次的分

| 数据集 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |
|---|---:|---:|---:|---:|---:|---:|---:|
| LoCoMo | 1536 | 56.5% | 51.0% | 0.427 | 64.0% | 57.8% | 0/450 |
| LongMemEval-S | 500 | 96.6% | 90.1% | 0.907 | 98.4% | 94.8% | — |

这是检索命中，不是模型答题分。

### 和公开检索分对照

公开列来自论文里的检索数字，这次列就是上面的主榜。差值 = 这次 − 公开，单位是百分点。LoCoMo 论文的口径是对话 recall accuracy，这次 k=5 用 Hit@5、k=10 用 Hit@10。论文总平均含对抗题，这次的总平均是 1536 道可答题。ECHO 写明 LoCoMo 的 Hit@10 是开发测量，查询扩展可能沾了题面。

| 对照 | 分项 | 公开 | 这次 | 差值 |
|---|---|---:|---:|---:|
| LoCoMo 论文，对话 k=5 | 单跳 | 68.0 | 38.7 | −29.3 |
| LoCoMo 论文，对话 k=5 | 多跳 | 35.4 | 63.2 | +27.8 |
| LoCoMo 论文，对话 k=5 | 时间 | 70.4 | 31.5 | −38.9 |
| LoCoMo 论文，对话 k=5 | 开放域 | 33.1 | 62.7 | +29.6 |
| LoCoMo 论文，对话 k=5 | 总平均，论文含对抗题 | 56.7 | 56.5 | −0.2 |
| LoCoMo 论文，对话 k=10 | 总平均，论文含对抗题 | 66.2 | 64.0 | −2.2 |
| ECHO，LoCoMo 1536 题 | Hit@10，开发测量 | 96.29 | 64.0 | −32.3 |
| ECHO，LongMemEval-S 500 题 | Hit@10 | 97.60 | 98.4 | +0.8 |
| ECHO，LongMemEval-S | 会话 Recall@5 | 88.71 | 90.1 | +1.4 |

### 差值

![LongMemEval 同一档，LoCoMo 总平均挨着论文。右边高于公开检索数字，左边低于。](benchmark/results/recall-20261028/delta.png)

前四行的差值不到 5 个点。后面几行是拉开的格子：多跳和开放域更高，单跳、时间题，以及对 ECHO 的 LoCoMo Hit@10 更低。

| 对照 | 这次 | 公开 | 差值 |
|---|---:|---:|---:|
| LongMemEval-S Hit@10 | 98.4 | 97.60 | +0.8 |
| LongMemEval-S 会话 Recall@5 | 90.1 | 88.71 | +1.4 |
| LoCoMo 总平均 k=5 | 56.5 | 56.7 | −0.2 |
| LoCoMo 总平均 k=10 | 64.0 | 66.2 | −2.2 |
| LoCoMo 开放域 k=5 | 62.7 | 33.1 | +29.6 |
| LoCoMo 多跳 k=5 | 63.2 | 35.4 | +27.8 |
| LoCoMo 单跳 k=5 | 38.7 | 68.0 | −29.3 |
| LoCoMo 时间题 k=5 | 31.5 | 70.4 | −38.9 |
| LoCoMo Hit@10 对 ECHO | 64.0 | 96.29 | −32.3 |

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

然后 Settings → MCP 确认 `rsi-boot` 已启用。

不要把这段配进用户级 `mcp.json`。那里的 `${workspaceFolder}` 经常不展开，记忆会串到别的仓库。要记哪个项目，就写在那个项目里。

## 接到其他宿主

### Claude Code

插件市场方式（推荐，可随仓库更新）：

```text
/plugin marketplace add Apollo-lwx/rsi-boot
/plugin install rsi-boot@rsi-boot
```

或一条命令直接装 MCP：

```bash
claude mcp add rsi-boot -- uvx --from rsi-boot rsi serve
```

在要记的那个仓库目录里打开 Claude Code。状态栏是可选项，见下文。

### Qoder

```bash
qoder mcp add rsi-boot -- rsi serve
```

或在项目根 `.mcp.json` 加与 Cursor 相同的 `mcpServers` 片段。同样要写在这个项目里，不要写进用户级配置。

### Codex

一行命令接入（推荐）：

```bash
codex mcp add rsi-boot -- uvx --from rsi-boot rsi serve
```

或手写 TOML 配置：用户级 `~/.codex/config.toml`，或项目级 `.codex/config.toml`（需目录已被信任）：

```toml
[mcp_servers.rsi-boot]
command = "rsi"
args = ["serve"]
```

从项目根目录启动 codex。要钉死目录，在上面的表里加一行：`env = { RSI_PROJECT_ROOT = "/绝对/路径" }`。

### DeepSeek Harness

在要记的那个仓库目录里打开桌面端。插件页安装 `dsh-rsi-boot`，或在已经能用 `dsh` 的终端里执行：

```bash
dsh plugin --profile web add dsh-rsi-boot
```

本机需要 Python 3.10+，并且终端里能运行 `rsi serve`。装完重启一次。工具名多一层前缀，例如 `mcp__rsi-boot__rsi_recall`。不要从用户主目录启动，否则 `.rsi/` 可能写进主目录。

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

六档预设：`mixed` 混搭（默认）、`buddy` 陪伴、`serious` 严肃、`healing` 治愈、`geek` 极客、`lucky` 好运。改完下一次刷新就生效。

风格写在 statusLine 的命令里，或写在 config.yaml 的 `statusline_style`。不要写进 `mcp.json`，那是记忆服务的配置。语言同样：命令加 `--lang zh`，或在项目 `.rsi/config.yaml` 写 `lang: zh`。想换一批文案，对代理说「生成状态栏文案」，写完跑 `rsi statusline --validate-quips`。

## 记忆大屏

不用改 `rsi serve` 的启动参数。对当前项目窗口里的代理说「打开记忆大屏」。说「关掉记忆大屏」后，下面这个地址立刻访问不到。

默认地址（端口被占用时，以代理回报的地址为准）：

```text
http://127.0.0.1:18765/dashboard
```

页面读的是当前仓库 `.rsi/`，不需要 API Key。

![RSI 记忆大屏](assets/dashboard-preview.png)

## 在对话里用

接好之后，对当前项目窗口里的代理说。不用自己开终端跑 `rsi`。

| 你说 | 结果 |
|------|------|
| 第一次学这个项目 | 扫这个仓库，写成待审条目；你批准之后，下次才能召回 |
| 重新学习 | 清掉这份仓库的记忆，再学一遍 |
| 打开记忆大屏 / 关掉记忆大屏 | 本机页面打开或关掉 |
| 把刚才的修法记下来 | 先进入待审，批准后才留下 |
| 这两条是不是矛盾 | 查冲突，等你裁决 |

重新学习会删掉该项目 `.rsi` 里的记忆、日志和缓存，保留 `identity.json`。文件被占着，就先在 Settings → MCP 关掉 `rsi-boot`。学完看 `.rsi/bootstrap_report.md`：待审清空并且批准过，召回才可用。代理如果报必须指定 `--host-judge`，让它加上这个参数再跑。

## 工具

接上之后，这些工具出现在当前项目的代理里。DeepSeek Harness 上的名字多一层前缀，例如 `mcp__rsi-boot__rsi_recall`。

| 工具 | 干什么 |
|------|--------|
| `rsi_recall` | 开工前召回。禁止项放在最前，后面是约定、文档和修过的教训 |
| `rsi_explore` | 按板块看知识树。不传路径时列出顶层和条数；传路径时列出这一支的标题和 id |
| `rsi_learn` | 第一次学这个仓库、把这次的教训记下来、做会话收工 |
| `rsi_knowledge_add` | 新增一条记忆。先待审，批准后才进入召回 |
| `rsi_knowledge_search` | 按问题查找已有记忆 |
| `rsi_knowledge_update` | 改已有的一条，或把近重复的几条合成一条 |
| `rsi_knowledge_delete` | 删除一条记忆 |
| `rsi_knowledge_review` | 批准或拒绝待审条目 |
| `rsi_conflicts` | 查出互相矛盾的记忆，等你裁决 |
| `rsi_memory_health` | 查出写错或来源已经失效的正式记忆。扫描本身不改库，确认之后才归档 |
| `rsi_memory` | 看索引、打开一条、重建索引、看条目之间的关系 |
| `rsi_feedback` | 记下这次召回有没有被用上。拒绝并写明原因时，会收成一条禁止项 |
| `rsi_review` | 看 harness 的改进提案和配置快照，决定留不留 |
| `rsi_stats` | 看记忆用了多少、采纳得怎么样 |
| `rsi_impact` | 看注入了多少、召回压了多少、采纳和合规 |
| `rsi_web_ui` | 打开或关掉本机记忆大屏。不改 `rsi serve` 的启动参数 |

字段和调用参数见 [`SKILL.md`](SKILL.md)。

## 配置

配置链：包内 `default.yaml` → `~/.rsi/config.yaml` → 项目根 `rsi-boot.yaml` → 请求参数。

`RSI_HOME` 只覆盖用户配置目录（默认 `~/.rsi`）。项目记忆在工作目录 `.rsi/`，打开另一个仓库就是另一份。主链路不用配；向量检索、模型提取这类增强要自己的 Key，示例在 `~/.rsi/config.yaml`。

项目里的 `rsi-boot.yaml` 不能写明文密钥，发现了就拒绝启动。

## 开发

改过本仓库代码后，在 Settings → MCP 重载 `rsi-boot`。

```bash
pip install -e ".[dev]"
python -m pytest
python scripts/export_schemas.py
```

不经对话、直接调命令时，写盘要带 `--host-judge` 或 `--local-judge`（`--dry-run` 不用）。旧的 `.rsi/rsi.db` 用 `rsi memory migrate` 迁到文件库。
