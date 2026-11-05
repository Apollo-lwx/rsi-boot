# 跨宿主一致性

同一份 .rsi，各宿主对同一组查询的 top-5 召回比较。
RSI 两次自读应完全一致（Jaccard=1.0），是宿主间比较可信的基座。

| 宿主对 | 平均 Jaccard@5 | 首位一致率 |
|---|---:|---:|
| claude vs cursor | 1.000 | 100.0% |
| claude vs rsi-a | 1.000 | 100.0% |
| claude vs rsi-b | 1.000 | 100.0% |
| cursor vs rsi-a | 1.000 | 100.0% |
| cursor vs rsi-b | 1.000 | 100.0% |
| rsi-a vs rsi-b | 1.000 | 100.0% |
