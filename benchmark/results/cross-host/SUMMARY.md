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

## 不能证明什么

- Jaccard=1.0 证明同一 .rsi 在各宿主读出同一份召回，不证明各宿主模型以相同方式使用这些记忆。
- 覆盖 claude/cursor/rsi 自读，未覆盖全部宣称支持的宿主（Qoder、DeepSeek Harness 等）。
- 查询集规模有限，首位一致率不代表长尾查询。
