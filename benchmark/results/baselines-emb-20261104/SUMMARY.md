# 基线对比榜 · emb-20261104

与召回主榜同口径（Hit@5 等，检索命中而非模型答题分）。vanilla-rag 的 embedding 档位见「系统注记」：真向量（API 或本地开源模型）与 hashing 词袋下界不可混列，混用时会按数据集分别标注。full-context 是「全量塞上下文」上界参照，不参与胜负判定。skipped 的系统是缺依赖或缺 Key，不是 0 分。

## LoCoMo

| 系统 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rsi | 1536 | 62.0% | 56.4% | 0.422 | 70.3% | 64.0% | 0/450 |
| vanilla-rag | 1536 | 68.6% | 60.4% | 0.500 | 77.9% | 70.3% | 0/450 |
| full-context | 1536 | 2.3% | 1.4% | 0.009 | 3.9% | 2.6% | 0/450 |

## LongMemEval-S

| 系统 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rsi | 500 | 96.6% | 90.2% | 0.908 | 98.4% | 94.8% | — |
| vanilla-rag | 500 | 69.2% | 55.4% | 0.520 | 83.4% | 70.6% | — |
| full-context | 500 | 17.6% | 8.6% | 0.080 | 35.4% | 19.8% | — |

## 系统注记

- rsi: pipeline=RecallService recall-balanced, cold start
- vanilla-rag: chunk=512 chars, overlap 64, embedding=LoCoMo: BAAI/bge-large-en-v1.5 (local fastembed/ONNX, no API key); LongMemEval-S: hashing-bow lower bound (512-char chunking of ~115k-token sessions yields ~564k chunks, offline embedding infeasible; embedding-backed LME run is future work)
- full-context: role=upper-bound reference, no retrieval
