# LLM-in-loop token 实测榜 · 2026-11-05

真实 API 计费的 token 研究（区别于 token-20261104 的离线 regex 口径）：
LoCoMo 分层抽样 80 题（seed 42，按题型等比），每题两臂各 2 轮，共 320 次调用。
执行器 `benchmark/token_study_llm.py`，逐次记录服务端 usage 到 `results.csv`。

## 协议与钉死项

- 端点：OpenAI 兼容 `https://api.moonshot.cn/v1`；模型 `kimi-k3`；max_tokens 512。
- 温度：Kimi K3 全系强制 temperature=1（400 拒绝其它值），协议的温度钉死
  在该端点不可行，如实披露；本研究主指标是服务端 prompt_tokens，温度无影响。
- 两臂提示词结构完全一致，仅上下文不同：no-rsi=整段对话原文（中位 5 万 token），
  with-rsi=RSI 冷启动召回 top-5 命中原文。
- 执行中程把单call超时 180s/重试3/max_tokens 1024 收紧为 90s/重试1/512
  （思考型模型长尾挂起），只影响 completion 尾部，不影响 prompt 侧主指标。

## 结果

| 指标 | no-rsi 臂 | with-rsi 臂 | 变化 |
|---|---:|---:|---:|
| prompt tokens 中位 | 50,307 | 610 | −98.8%（比值 1.21%） |
| prompt tokens 合计 | 7,630,712 | 96,092 | — |
| completion tokens 中位 | 242 | 192 | −20.7% |
| 墙钟延迟中位 | 11,960 ms | 7,398 ms | −38.1% |

配对 Wilcoxon（配对键=(qid, round)，n=160）：z = 10.97，p = 5.3e-28，
效应量 r = 0.867。中位每题节省 49,726 token。

与离线口径（token-20261104：中位压缩比 0.9%）互证：regex 分词与真实计费
token 的差异解释了 0.9% 与 1.21% 的小幅出入，结论同向。
