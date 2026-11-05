# 混合检索消融榜 · ablation-hybrid-20261105

LoCoMo 全量 10 样本（1,536 道有证据题），冷启动 `recall-balanced`，四臂对照：
`base/` 纯词面（线上默认）；`vector/` + fastembed/ONNX 本地 bge-large-en-v1.5
语义通道，门禁后 RRF(k=60) 融合（`RSI_ENABLE=vector`）；`multihop/` + 首跳桥接词
二跳级联（`RSI_ENABLE=multihop`）；`both/` 两者同开。向量通道与 vanilla-rag 臂
用同一模型，苹果对苹果。各臂子目录内含分片 jsonl 与各自 SUMMARY。

## 总表

| 臂 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 |
|---|---:|---:|---:|---:|---:|
| base（纯词面） | 62.0% | 56.4% | 0.422 | 70.3% | 64.0% |
| + multihop | 62.0% | 56.3% | 0.422 | 70.4% | 64.2% |
| + vector | **68.2%** | **61.6%** | **0.486** | **78.6%** | **71.6%** |
| + 两者 | 68.3% | 61.7% | 0.486 | 78.6% | 71.6% |

## 分题型（Hit@5）

| 题型 | base | multihop | vector | both |
|---|---:|---:|---:|---:|
| multi-hop | 39.4% | 39.4% | **48.9%** | **48.9%** |
| open-domain | 30.4% | 30.4% | **41.3%** | 41.3% |
| single-hop | 72.2% | 72.2% | **76.7%** | **76.8%** |
| temporal | 64.5% | 64.5% | **70.7%** | 70.7% |

## 显著性（vector vs base，逐题配对 McNemar 精确检验，Hit@5）

新增命中 98 题、丢失 3 题，p = 1.4e-25。分题型净增：multi-hop +27/−0、
open-domain +10/−0、single-hop +40/−2、temporal +21/−1。

## 结论

- 向量通道全面提升所有题型（Hit@5 +6.2pt，multi-hop +9.5pt），统计显著。
- 桥接词多跳级联在本基准上为中性（±0.2pt，噪声内），代码保留为默认关闭的
  实验开关，作为负结果如实报告。
- 向量通道默认关闭（产品默认零依赖、零下载）；开启方式：
  `rsi-boot.yaml` 的 `enable: [vector]` 或环境变量 `RSI_ENABLE=vector`，
  模型用 `RSI_VECTOR_MODEL` 覆盖（默认 bge-small-en-v1.5）。
