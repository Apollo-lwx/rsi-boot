# Cursor 侧跨宿主执行包

目标：让 Cursor 通过自己的 MCP 链路读同一份种子 `.rsi`，对 5 个固定查询各调一次
`rsi_recall`，把返回的记忆 id 顺序落盘成 `host-cursor.jsonl`，用于和 RSI 自读基座
（Jaccard=1.0）做一致性对比。

## 第 1 步：给 Cursor 配一个指向种子项目的 MCP 服务

在 Cursor 的 MCP 配置（`~/.cursor/mcp.json` 或项目级 `.cursor/mcp.json`）里加一条
**独立条目**（不要动你日常用的 rsi-boot 条目）：

```json
"rsi-cross-host": {
  "command": "D:\\project\\study\\rsi-boot\\.venv\\Scripts\\python.exe",
  "args": [
    "-X", "utf8", "-m", "rsi_boot", "serve",
    "--project-root", "D:\\project\\study\\rsi-boot\\benchmark\\.data\\cross-host-seed"
  ]
}
```

加完后在 Cursor 设置里确认 `rsi-cross-host` 显示为已连接（绿灯）。

## 第 2 步：把下面这段提示词原样发给 Cursor 的 Agent

```
请依次完成以下评测任务，全程不要改动任何项目代码：

1. 调用 MCP 工具 rsi_recall（来自 rsi-cross-host 服务），参数如下，共 5 次：
   - query="When are production deployments frozen?", top_k=5
   - query="What is the black line length for this project?", top_k=5
   - query="What caused the cache stampede incident and how was it fixed?", top_k=5
   - query="How do I rotate an API key?", top_k=5
   - query="Can I hand-edit generated protobuf files?", top_k=5
   （如该工具需要 user_id 参数，填 "cross-host"）

2. 每次调用从返回结果中按顺序取出所有 item 的 id 字段。

3. 把结果写入文件
   D:\project\study\rsi-boot\benchmark\results\cross-host\host-cursor.jsonl，
   每行一个 JSON 对象，格式 {"qid": "q1", "ids": ["...", "..."]}，
   qid 依次为 q1..q5，ids 保持返回顺序。写完后告诉我每行的内容。
```

## 第 3 步：回到本项目，汇总

```
.venv/Scripts/python.exe -X utf8 benchmark/cross_host.py --report
```

会输出 cursor 与 rsi 基座的 Jaccard@5 与首位一致率对照表
（benchmark/results/cross-host/SUMMARY.md）。

## 排错

- Cursor 里看不到 rsi_recall 工具 → MCP 条目没连上：检查 python 路径、
  `--project-root` 路径是否存在（种子已生成于 benchmark/.data/cross-host-seed）。
- 返回为空 → 确认 Cursor 连的是 rsi-cross-host 而不是日常的 rsi-boot 条目。
