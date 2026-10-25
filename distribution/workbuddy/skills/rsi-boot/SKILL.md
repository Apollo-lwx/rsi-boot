---
name: rsi-boot
display_name: RSI Boot 仓库记忆
display_name_en: RSI Boot
description: 让 AI 记住当前仓库怎么写代码：要遵守的写法、不能做的事、以前的 bug 怎么改的。用户说记住、规矩、别再犯、这个项目怎么写时使用。
description_zh: 让 AI 记住这个仓库怎么写代码。下次打开项目会照着做，换编辑器也不用重讲。
description_en: Helps the AI remember how this repo is written, and follow that the next time the project is opened.
version: 0.1.1
author: wenxing luo
---

# RSI Boot

让 AI 记住当前打开的仓库：要遵守的写法、不能做的事、以前的 bug 是怎么改的。这些记在仓库里的 `.rsi/`，换编辑器还在。不需要 API Key。下面只说明何时调用已经装好的工具。

连接器未出现 `rsi_*` 工具时，先让用户在「专家 · 技能 · 连接器」里启用 RSI Boot。不要另写一套 mcp.json。

## 步骤

1. 任务开始调用 `rsi_recall`。禁止项按硬约束执行。要看相邻代码或知识树时再用 `rsi_explore`；定点查找用 `rsi_knowledge_search`。
2. 按召回结果完成用户的任务。
3. 修了用户能看见的问题、被纠正了做法、或改了公共规则时，用 `rsi_learn` 或 `rsi_knowledge_add` 记下来。教训要写明错误做法和正确修法。
4. 新条目先是 pending。只有 `rsi_knowledge_review` 或 `rsi_review` 批准之后才进入召回。用了召回结果，收工前用返回的 `feedback_token` 调 `rsi_feedback`，动作为 `accepted` 或 `rejected`。

## 工具

| 工具 | 用途 |
| --- | --- |
| `rsi_recall` | 任务前拉取禁止项和约定 |
| `rsi_explore` | 看代码邻近和知识树 |
| `rsi_knowledge_search` | 按关键词查找 |
| `rsi_learn` | 蒸馏本轮教训 |
| `rsi_knowledge_add` / `rsi_knowledge_update` / `rsi_knowledge_delete` | 增改删知识 |
| `rsi_knowledge_review` / `rsi_review` | 批准或驳回 pending |
| `rsi_conflicts` | 处理互相矛盾的记忆 |
| `rsi_memory_health` | 检查过时或错误的记忆 |
| `rsi_feedback` | 上报召回是否被采纳 |
| `rsi_impact` / `rsi_stats` | 查看召回和采纳情况 |
| `rsi_memory` | 查看记忆状态 |

删除知识、驳回条目之前先跟用户确认。
