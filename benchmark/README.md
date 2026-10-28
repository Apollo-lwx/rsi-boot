# 召回主榜

公开数字只量一件事：问题抛过来，标准证据有没有出现在召回的前几条里。复跑：

```bash
python -X utf8 benchmark/recall_board.py
```

数据集放在 `benchmark/.data/`（不入库）。结果在 [results/recall-20261028/SUMMARY.md](results/recall-20261028/SUMMARY.md)。

## 指标

产品默认召回 5 条，上限 10。主数字是 **Hit@5**。另报 @10。Mem0 主页上的 92.5（LoCoMo）、94.4（LongMemEval）是模型答题后再由裁判打分，而且用的是 top 50 / top 200。那个百分数和 Hit@5 不写在同一列。

| 数据集 | 切分 | 标准证据 |
|---|---|---|
| LoCoMo（`locomo10.json`） | 一条官方 turn（`dia_id`）一条记忆，正文前加上该 session 的日期 | 类别 1–4 的 `evidence`。类别 5 答案为空，`evidence` 是干扰项，只计入「该空时空了」 |
| LongMemEval-S cleaned | 一条 haystack session 一条记忆。超过 18000 字再切开，同一 session 的切片都算这条证据 | `answer_session_ids` |

问题原文就是 `rsi recall` 的任务文本。走冷启动 `recall-balanced`。不调用模型答题。

| 指标 | 分母 | 一次什么叫中 |
|---|---|---|
| Hit@k | 有标准证据的题 | 前 k 条里至少有一个标准证据 |
| Recall@k | 该题的标准证据条数 | 前 k 条盖住了其中多少 |
| MRR | 有标准证据的题 | 第一条命中排在第几，取倒数再平均 |
| 该空时空了 | 标准证据为空的题 | 前 5 条是空的 |

token 宿主没有报，记 null。

仓库约定榜和「没有这条记忆就修不对」的修对榜还没有题集，不进这张表。
