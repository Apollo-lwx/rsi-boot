# DeepSeek Harness 插件

这个目录只放 DeepSeek Harness 桌面端的安装包。Cursor、Qoder、Claude Code 的清单仍在仓库根。

装上之后，Harness 会在本机拉起 `rsi serve`。约定、禁止项和修 bug 的经验仍写在当前仓库的 `.rsi/`，和 Cursor、Claude Code、Codex 用的是同一份。主链路不上传对话，也不需要 API Key。

## 使用前

1. 本机已安装 Python 3.10+，并且终端里能执行 `rsi serve`（`pip install rsi-boot`）。
2. 桌面端是 DeepSeek Harness v0.2。插件声明的范围是 `>=0.2.0-0 <0.3.0`（含预览版）。
3. 在**要记的那个仓库的目录**里打开 Harness。桌面端的 MCP 客户端不会告诉 rsi 当前窗口是哪个文件夹，子进程只继承 Harness 自己的工作目录。从别的目录启动时，记忆会跟到那个目录；从用户主目录启动时，有机会把 `.rsi/` 写进主目录。
4. 一个 Harness 进程只绑它启动时认领到的那一个仓库。换仓库时，先关掉再从另一个仓库目录打开，或按下面的覆盖把 `cwd` 和 `RSI_PROJECT_ROOT` 改成那个仓库的绝对路径。

要钉死某一个仓库，在 profile 的 `cordis.patch.yml`（或 `$DSH_HOME/cordis.patch.yml`）里追加**同 id、只写 config** 的覆盖。覆盖会整段换掉 config，字段要写全：

```yaml
- id: mcp-rsi-boot
  config:
    serverName: rsi-boot
    transport: stdio
    command: rsi
    args: [serve]
    cwd: D:/work/my-repo
    env:
      RSI_PROJECT_ROOT: D:/work/my-repo
      PYTHONUNBUFFERED: "1"
      PYTHONIOENCODING: utf-8
    failOnStartupError: false
    toolCallTimeoutMs: 300000
```

不要再写一条带 `name` 的完整 `insert`，否则会和本包的 `mcp-rsi-boot` 撞车，Harness 起不来。

## 安装

桌面端：插件页输入 npm 包名 `dsh-rsi-boot`。包发到 npmjs 之后这一步才能搜到。发版命令在文末，本仓库默认的 npm 源是内网，不要发到那里。

已经能用菜单里的 `dsh` 命令时：

```bash
dsh plugin --profile web add dsh-rsi-boot
```

源码还没发 npm、但 GitHub 上已经有这个目录时：

```bash
dsh plugin --profile web add github:Apollo-lwx/rsi-boot/distribution/deepseek-harness
```

装完重启一次 Harness。对话里应能看到 `mcp__rsi-boot__rsi_recall`、`mcp__rsi-boot__rsi_explore` 这类工具。名字比 Cursor 里多了 `mcp__rsi-boot__` 前缀，干的是同一批事。

本仓库根上已有 `SKILL.md`。给整个仓库打 GitHub topic `dsh-plugin` 时，社区市场会先把它当成技能，而不会安装这个子目录里的插件。桌面端请走上面的包名或 `github:.../distribution/deepseek-harness`。

## 和同事、别的 AI 共用

Harness 读到的还是仓库里的 `.rsi/` 和根上的 `AGENTS.md`。怎么提交给同事，见仓库根 README 里「跨 AI 工具、多人共用」那一段。

## 卸载

```bash
dsh plugin --profile web remove dsh-rsi-boot
```

若手动覆盖过 `mcp-rsi-boot`，把那段 config 一并删掉，然后重启。

## 数据去哪

| 项 | 行为 |
| --- | --- |
| 云端 | 主链路不连网、不调用模型 |
| 落盘 | 当前仓库的 `.rsi/`，外加根目录 `AGENTS.md` 里的托管块 |
| 密钥 | 插件不保存 Key。用户自己在 `~/.rsi/config.yaml` 打开模型增强时，才会访问他填写的接口 |
| 权限 | 启动本机已安装的 `rsi`。`failOnStartupError` 为 false：`rsi` 不在 PATH 里时，Harness 照常打开，只是没有这组工具 |

## 发到桌面端能搜到的 npm

公开注册表上的包名是 `dsh-rsi-boot`（截至编写时无人占用）。本机若把 npm 源指到了内网，发布必须显式写注册表：

```bash
cd distribution/deepseek-harness
npm login --registry https://registry.npmjs.org
npm publish --registry https://registry.npmjs.org --access public
```

改过插件再发时先 bump `package.json` 的 `version`。已经发出去的版本号不能覆盖。
