# 受控 token 研究 · 20261104
tokenizer: regex-approx (no tiktoken; same ruler for both arms)（两臂同一把尺子，配对比较）
- 题数：2036（>=60 要求：满足）
- full-context 臂上下文 token 中位数：50723
- rsi 臂上下文 token 中位数：460
- 中位压缩比（rsi/full）：0.9%（即减少 99.1%）
- 配对 Wilcoxon：n=2036，z=39.08
- p（双侧）：<0.001，效应量 r=0.866
口径声明：这是「送入模型的上下文 token 数」测量，不含模型生成；真实 LLM-in-loop 的 usage token 研究用 --prepare 生成的协议执行。
