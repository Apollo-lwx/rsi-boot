# 基线对比榜 · 20261104

与召回主榜同口径（Hit@5 等，检索命中而非模型答题分）。vanilla-rag 无 OPENAI_API_KEY 时用 hashing 词袋向量，是下界参照，不能与真向量混列。full-context 是「全量塞上下文」上界参照，不参与胜负判定。skipped 的系统是缺依赖或缺 Key，不是 0 分。

## LoCoMo

| 系统 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rsi | 1536 | 62.0% | 56.4% | 0.422 | 70.3% | 64.0% | 0/450 |
| vanilla-rag | 1536 | 33.7% | 29.9% | 0.227 | 41.7% | 37.0% | 0/450 |
| full-context | 1536 | 2.3% | 1.4% | 0.009 | 3.9% | 2.6% | 0/450 |

## LongMemEval-S

| 系统 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rsi | 500 | 96.6% | 90.2% | 0.908 | 98.4% | 94.8% | — |
| vanilla-rag | 500 | 69.2% | 55.4% | 0.520 | 83.4% | 70.6% | — |
| full-context | 500 | 17.6% | 8.6% | 0.080 | 35.4% | 19.8% | — |

## 系统注记

- rsi: pipeline=RecallService recall-balanced, cold start
- vanilla-rag: chunk=512 chars, overlap 64, embedding=hashing-bow (fallback, no OPENAI_API_KEY)
- full-context: role=upper-bound reference, no retrieval
