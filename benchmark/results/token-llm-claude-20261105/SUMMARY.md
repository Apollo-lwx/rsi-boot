# LLM-in-loop token 实测榜（Claude 复现） · 2026-11-05

对 token-llm-20261105（Kimi K3）的同协议第二厂商复现：LoCoMo 分层抽样
80 题（seed 42，与 Kimi 轮完全同批），每题两臂各 2 轮，共 320 次调用。
执行器 `benchmark/token_study_llm.py --provider=anthropic`，逐次记录服务端
usage 到 `results.csv`。

## 协议与钉死项

- 端点：Anthropic Messages 兼容中转 `https://sub.sundayrouter.net`；
  模型 `claude-haiku-5-5`；max_tokens 512；温度参数省略（同 Kimi 轮口径，
  主指标为服务端 prompt_tokens，温度无影响）。
- 抽样种子、两臂构造（no-rsi=整段对话原文 / with-rsi=RSI 冷启动召回 top-5
  命中原文）、配对键=(qid, round) 与 Kimi 轮逐题一致。
- **口径披露**：该中转按 Claude Code 客户端身份放行，网关在每次请求注入
  约 1.9k token 的客户端系统提示开销（空载探针 input_tokens=1896）。
  该开销对两臂等额，方向上会**缩小**实测压缩比（with-rsi 臂占比被抬高），
  因此下表的 −96.4% 是保守下界；剔除该固定开销后与 Kimi 轮的 1.21% 基本一致。

## 结果

| 指标 | no-rsi 臂 | with-rsi 臂 | 变化 |
|---|---:|---:|---:|
| prompt tokens 中位 | 75,539.5 | 2,688 | −96.4%（比值 3.56%） |
| prompt tokens 合计 | 11,610,105 | 429,097 | — |
| completion tokens 中位 | 73 | 62 | −15.1% |
| 墙钟延迟中位 | 5,032 ms | 2,171 ms | −56.9% |

配对 Wilcoxon（n=160）：z = 10.97，p = 5.3e-28，效应量 r = 0.867；
160/160 对全部改善。中位每题节省 72,985.5 token。

## 结论

跨厂商、跨分词器复现成立：prompt 侧压缩两个数量级的结论不依赖 Kimi 端点。
两轮的显著性统计 identical（全部配对同向改善时 Wilcoxon 只取决于配对数），
中位压缩比的差异（1.21% vs 3.56%）由分词器差异与网关固定开销解释，方向一致。
