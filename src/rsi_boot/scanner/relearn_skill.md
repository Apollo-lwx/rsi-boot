# rsi-relearn

重新学习项目知识时按阅读包蒸馏短知识。不点名任何一家宿主。

## 步骤

1. 要清空文件记忆时先 `rsi wipe --yes`（会清掉 `reading-packs`）。
2. `rsi bootstrap --consent --host-judge`。缺旗标时补 `--host-judge`，不要改用 `--local-judge`。
3. `rsi_learn` 的 `pack_list` 看 `lanes`（代码 / skills+rules / git / docs / teaching / 对话 / `.cursor` / 其余）。**本轮必须把全部 pending 蒸完**，禁止说「下次再学」或只蒸一波就停。
4. 按板块**并行**派生子代理：代码一个、skills+rules 一个、git 一个、docs 一个，其余板块同样各派一个。板块之间默认不打架；真冲突留给召回 / `rsi_conflicts`。每个子代理只处理本 lane 的 `pending_ids`（也可用 `pack_list` 带 `lane=`）。
5. 子代理对每包 `pack_open`，按**每一条**路径读源（docs、实际代码、git-fix 与 `.cursor` 同等对待），`rsi_knowledge_search` 后 `rsi_knowledge_add`（必须带 `pack_id`），再 `pack_done`。`knowledge_add` 按包域落目录：skill / teaching_case / gene_case / pattern / convention / documentation。源路径含 `gene-map` / `teaching-cases` / `patterns` 时按 gene_case / teaching_case / pattern 写，即使包域是 conversation。skills / rules / teaching / gene-map / patterns **一条源至少一条知识**（每包最多 20）。
6. **只有 README 文件名或路径落在 `.cursor/audit-result`、`.rsi/audit` 可以 `skipped`。** 对话板已有几条 done、version-mirror / clone / catalog、产品路径含 `ats-` / `audit` 子串，一律必须蒸馏。`pack_done skipped` 被拒就继续蒸。`pack_list reopen_skipped=true` 会把误 skip 以及 index/yaml 状态不一致的包改回 pending。
7. 父代理等全部子代理结束后看报告：skipped 算未覆盖，不是学完。代码 / 对话 / `.cursor` / docs 任一板 pending 或整板空着都不得声称学完。`pack_list` pending=0 后，`rsi_knowledge_review` 带 `bootstrap_run_id` 批准本 run。未批准不得声称召回可用。

## 约束

- 禁止 Jaccard、分词出门或把全文入库。
- **禁止**把整本 skill / teaching / gene-map 收成一条「目录综述」。独立 skill、rule、teaching-case、gene-map、pattern 各写各的。
- 代码按模块写约定/禁止项，不要只留一条总架构。
- `refs` 只作出处。
- `rsi_conflicts` 只处理短知识 id。
- **禁止**在蒸馏中途调用 `mcp_auth` 或给用户弹授权。RSI 无 OAuth。工具报 error / needsAuth 时重试 `rsi_learn` / `rsi_knowledge_add`，不要授权 Figma / SonarQube / 其它 MCP。一轮不要并行打爆同一 stdio（每批 knowledge_add / pack_done 不超过 3 个）。
