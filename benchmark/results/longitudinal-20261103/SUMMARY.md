# 纵向自改进实验 · 20261103

治理环传导性：合成 rejected 反馈 → pending 草稿 → 人工确认 → official，
逐轮跑固定探针（stable-* 为回归护栏，learn-N 为第 N 轮注入的知识）。
no-promotion 为消融臂（反馈永不确认晋升）。

| 臂 | 轮次 | stable 通过率 | learn 通过率 |
|---|---:|---:|---:|
| governed | 0 | 2/2 | 0/3 |
| governed | 1 | 2/2 | 1/3 |
| governed | 2 | 2/2 | 2/3 |
| governed | 3 | 2/2 | 3/3 |
| no-promotion | 0 | 2/2 | 0/3 |
| no-promotion | 1 | 2/2 | 0/3 |
| no-promotion | 2 | 2/2 | 0/3 |
| no-promotion | 3 | 2/2 | 0/3 |

口径声明：合成反馈是受控信号（规则文本直接对应探针 needle），测的是
治理环是否传导信号；不是真实用户语料上的效果声明。
