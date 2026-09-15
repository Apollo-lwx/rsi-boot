# rsi-relearn

重新学习项目知识时按阅读包蒸馏短知识。不点名任何一家宿主。

## 步骤

1. 要清空文件记忆时先 `rsi wipe --yes`（会清掉 `reading-packs`）。
2. `rsi bootstrap --consent --host-judge`。缺旗标时补 `--host-judge`，不要改用 `--local-judge`。
3. `rsi_learn` 的 `pack_list` 看待读包；`pack_open` 打开一包。
4. 按包内路径读源，用 `rsi_knowledge_search` 查已有短知识，再 `rsi_knowledge_add` 写入自包含短知识。
5. `pack_done` 标完成；写不出则 `skipped` 并写原因。
6. `rsi_knowledge_review` 批准本 run。未批准不得声称召回可用。

## 约束

- 禁止 Jaccard、分词出门或把全文入库。
- 一包写 1–20 条。`refs` 只作出处。
- `rsi_conflicts` 只处理短知识 id。
