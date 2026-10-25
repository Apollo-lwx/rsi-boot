# WorkBuddy 连接器

这个目录只放 WorkBuddy 上架材料。Cursor、Qoder、Claude Code 的清单仍在仓库根，不写到这里。

上传用的压缩包根目录只能有这四项：

```text
connector-meta.json
mcp.json
icon.svg
skills/
```

本 README 不要打进 zip。在本目录执行：

```powershell
tar -a -c -f rsi-boot-workbuddy.zip connector-meta.json mcp.json icon.svg skills
```

生成的 zip 是提交物，不入库。

## 提交

1. 确认 PyPI 上的 `rsi-boot` 版本与 `connector-meta.json` 的 `version` 一致（当前 `0.1.1`），本机能执行 `uvx --from rsi-boot rsi serve`。
2. 在 [open.workbuddy.cn](https://open.workbuddy.cn) 完成开发者认证后，创建连接器并上传 zip。解析失败对照 [连接器规范](https://open.workbuddy.cn/docs/connector)；仍对不上就发 `openworkbuddy@tencent.com`。
3. 审核前用两个不同仓库各召回一次，确认各自只读到自己的 `.rsi/`。`mcp.json` 不写 `RSI_PROJECT_ROOT`，工作区靠 MCP roots 和进程 cwd 认领。
4. 审核通过后约 10～15 分钟出现在连接器市场。更新时先发 PyPI，再递增这里的 `version`，重新打包提交。

`icon.svg` 来自仓库 `assets/logo.svg`（深色底板）。市场若要求透明底，换掉同名文件后再打包。

stdio 的 `runtime` 目前只支持 Node。这份配置用 `uvx` 拉 Python 包，提交前在一台没装过 rsi-boot 的 WorkBuddy 上确认能拉起，并且工具列表里有 `rsi_recall`。若审核要求运行时由平台托管，先问 WorkBuddy 能否为 stdio MCP 声明 Python runtime。

规范入口：[入驻](https://open.workbuddy.cn/docs/onboarding)、[连接器](https://open.workbuddy.cn/docs/connector)、[技能](https://open.workbuddy.cn/docs/skill)。
