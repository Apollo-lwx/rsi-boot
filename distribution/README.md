# 分发材料

每个上架平台单独一个目录，规范只写在自己的目录里。

| 目录 | 平台 |
| --- | --- |
| [`workbuddy/`](workbuddy/) | WorkBuddy 连接器市场 |
| [`deepseek-harness/`](deepseek-harness/) | DeepSeek Harness 桌面端插件 |

宿主规定必须放在仓库根的清单留在原处，不搬进这里：

- `.cursor-plugin/`
- `.qoder-plugin/`
- `.claude-plugin/`
- 根目录 `mcp.json`（Cursor 与 Qoder 插件共用）

新增平台时建 `distribution/<平台名>/`。
