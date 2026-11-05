# 答题准确率榜（LLM judge） · 2026-11-05

补「检索命中 → 答对」转化漏斗。与 token-llm 榜同批 LoCoMo 分层抽样 80 题
（seed 42），三臂上下文：full=整段对话原文 / rsi=冷启动召回 top-5 /
rag=vanilla-rag top-5（bge-large 本地向量）。两台生成器（kimi-k3、
claude-haiku-5-5）各自答题，**双 judge 交叉互判**（每份生成同时被两家判），
金标准取 LoCoMo 原始答案；category 5 不可答题以「正确拒答」为对。

执行器 `benchmark/answer_accuracy.py`（--generate / --judge / --analyze，
全程幂等续跑）。原始数据：generations.jsonl（480 份，含 usage）与
judgments.jsonl（960 份判定）。

## 结果（双 judge 全对才算对，保守合并）

| 生成器 | full | rsi | rag |
|---|---:|---:|---:|
| Claude Haiku | 66.2% | 52.5% | 48.7% |
| Kimi K3 | 62.5% | 46.3% | 46.3% |

- judge 间一致率 87.5%（420/480）。
- McNemar 精确双侧：rsi vs rag p=0.70（claude）/ 1.00（kimi）——**答题层面
  rsi 与向量基线无显著差异**，LoCoMo 检索命中率差 6.6 点没有转化到答题；
  rsi vs full p=0.09 / 0.02——全量上下文仍最强，这是 98.8% 上下文削减的
  真实代价，如实报告。
- 不可答题拒答可靠性：rsi 64% > full 50% > rag 39%（约束召回抑制幻觉）。
- multi-hop 是真短板：rsi 5% vs rag 32% vs full 59%（证据跨会话分散，
  top-5 窗口装不下）。

## 按题型（两生成器合并，n 为题数×2）

| 题型 | full | rsi | rag |
|---|---:|---:|---:|
| adversarial | 50% | **64%** | 39% |
| multi-hop | 59% | 5% | 32% |
| open-domain | 75% | 62% | 38% |
| single-hop | 63% | 50% | 56% |
| temporal | 88% | 62% | 54% |

## 披露

- 中途 Kimi 账户余额耗尽（429），生成与评判分两段完成；续跑幂等，
  对结果无影响（逐条落盘）。
- judge 用与生成器同款的 kimi-k3 / claude-haiku-5-5（交叉互判，
  不存在自判自）；judge 温度端点强制为 1（K3 平台约束），已在
  token-llm 榜同样披露。
- 合并口径偏保守（双判全对才算对），对任何臂不利方向一致。
