<!-- rsi-boot:begin -->

> 本块由 RSI Boot 自动维护，请勿手改（改动会被下次重写覆盖）。

## 记忆目录（详情用 rsi_recall 召回）
- 2026-09-10-rsi-boot-spec.md# 10.9.10 去重与增量更新
- 分词去掉贴在词上的标点
- 分词去掉贴在词上的标点
- Review host-distill-knowledge implementation against plan tasks 1-13 and spec §4/§7/§11: bootstrap reading packs, Jaccar
- mcp 下限改为 >=1.30.0,<2，Jinja2 下限改为 >=3.1.6。密钥只解析用户配置里写明的 env:变量名。scan_conversations 默认不读文件，agent-transcripts 与 IDE jsonl 不
- mcp 下限改为 >=1.30.0,<2，Jinja2 下限改为 >=3.1.6。密钥只解析用户配置里写明的 env:变量名。scan_conversations 默认不读文件，agent-transcripts 与 IDE jsonl 不
- 市场上架文案用大白话说明产品
- 市场上架文案用大白话说明产品
- 分发平台规范按平台分目录
- 分发平台规范按平台分目录
- 单独开记忆健康车道：整表归因到每个 retrieved id；词表极性三分类复用 ConflictDetector._polarity；矛盾才开 incorrect，来源哈希变且标题短语不在文件中才开 stale_source；扫描只写发现
- 单独开记忆健康车道：整表归因到每个 retrieved id；词表极性三分类复用 ConflictDetector._polarity；矛盾才开 incorrect，来源哈希变且标题短语不在文件中才开 stale_source；扫描只写发现
- 列出列名
- 列出列名
- MCP server 的工作区边界用 roots/list 协议拿，别信进程 cwd
- resolve_project_root 不得信任 VSCODE_CWD
- resolve_project_root 不得信任 VSCODE_CWD
- Cursor agent 终端跑 rsi CLI 必须显式 --project-root
- pytest basetemp 不得放在被测仓库内
- 密域蒸馏条按包域改回 skill/teaching/gene/pattern
- 密域蒸馏条按包域改回 skill/teaching/gene/pattern
- 蒸馏中途授权是宿主调了 mcp_auth
- 蒸馏中途授权是宿主调了 mcp_auth
- 宿主蒸馏禁止「蒸几条示例就 skip 整板」
- 宿主蒸馏禁止「蒸几条示例就 skip 整板」
- skip 算未覆盖，禁止整板 skip，蒸馏按源类型落目录
- skip 算未覆盖，禁止整板 skip，蒸馏按源类型落目录
- 按板块并行本轮蒸完，报告给人看不要下一步
- 按板块并行本轮蒸完，报告给人看不要下一步
- 解释 user-skill-audit 是什么：RSI skill 还是其它组件，以及为何 audit_finish 跳过它
- RSI 收工只走 rsi_learn 的 audit_start/audit_probes/audit_report/audit_finish。Cursor 命名空间 user-skill-audit 来自用户级 mcp.json 的 sk

## 知识树（板块计数；下钻用 rsi_explore，召回用 rsi_recall）
- bootstrap (89)  ·  rsi_explore "bootstrap"
- general (51)  ·  rsi_explore "general"

## 收工纪律

teach_catch：修失败多次、低置信、改公共规则、或用户纠正执行方式时先记现场；写好 lesson 后 teach_record。
修了用户可见的 bug（成功也算）必须 teach_record，lesson 含 wrong_action 与 correct_fix。
未 audit_finish 前不得改业务代码。
最终总结照抄 closeout 路径，或写跳过原因。
用了 rsi_recall 命中项，收工前必须 rsi_feedback（带 feedback_token）上报 accepted 或 rejected。
Catch then record. Do not edit product code before the audit is finished. Copy closeout paths into the final summary.

<!-- rsi-boot:end -->
