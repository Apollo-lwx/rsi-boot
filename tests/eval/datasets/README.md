# 评测数据集标注规范（§12）

数据集随仓库版本化。三来源：模板生成（`gen_*.py`，确定性 seed）+ 人工标注 + 脱敏日志回流（后两者后续追加，追加时保持 JSONL 格式与字段不变）。

## intent_general.jsonl / intent_role.jsonl（§12.1）

- 字段：`{"input": str, "intent": str, "role": str?}`（role 仅角色集）
- 每意图 ≥ 50 条；通用意图与角色意图分开统计
- 人工标注追加规则：一条输入只标一个主意图；无法判断的样本不入集

## retrieval_corpus.jsonl / retrieval_queries.jsonl（§12.2）

- 语料：`{"id", "project_type", "title", "content"}`；查询：`{"query", "relevant": [doc_id], "project_type"}`
- 覆盖 ≥ 3 类项目（web_backend / frontend / cli_tool），每类 ≥ 30 条查询
- 合成语料主题间词汇低重叠；真实项目语料追加时需脱敏（§8.1 规则）

## 重新生成

```bash
python -X utf8 -m tests.eval.datasets.gen_intent_dataset
python -X utf8 -m tests.eval.datasets.gen_retrieval_dataset
```

生成器确定性（固定 seed）；修改模板等于变更评测集，需在 PR 中说明并重跑全量评测归档报告。
