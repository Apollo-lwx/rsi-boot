# 纵向自改进实验 v2 · 20261104

10 轮合成事件流，三臂对比。stable=回归护栏（2 题），learn=当轮注入
知识（10 题），噪声暴露率=stable 查询 top-5 出现错误反馈文档的比例。

| 臂 | 轮次 | stable | learn | 噪声暴露 |
|---|---:|---:|---:|---:|
| governed | 0 | 2/2 | 0/10 | 0.00 |
| governed | 1 | 2/2 | 1/10 | 0.00 |
| governed | 2 | 2/2 | 2/10 | 0.00 |
| governed | 3 | 2/2 | 3/10 | 0.00 |
| governed | 4 | 2/2 | 4/10 | 0.00 |
| governed | 5 | 2/2 | 5/10 | 0.00 |
| governed | 6 | 1/2 | 6/10 | 0.00 |
| governed | 7 | 1/2 | 7/10 | 0.00 |
| governed | 8 | 1/2 | 8/10 | 0.00 |
| governed | 9 | 1/2 | 9/10 | 0.00 |
| governed | 10 | 1/2 | 9/10 | 0.00 |
| approve-all | 0 | 2/2 | 0/10 | 0.00 |
| approve-all | 1 | 2/2 | 1/10 | 0.00 |
| approve-all | 2 | 2/2 | 2/10 | 0.00 |
| approve-all | 3 | 2/2 | 3/10 | 1.00 |
| approve-all | 4 | 1/2 | 4/10 | 1.00 |
| approve-all | 5 | 1/2 | 5/10 | 1.00 |
| approve-all | 6 | 1/2 | 6/10 | 1.00 |
| approve-all | 7 | 1/2 | 7/10 | 1.00 |
| approve-all | 8 | 1/2 | 8/10 | 1.00 |
| approve-all | 9 | 1/2 | 8/10 | 1.00 |
| approve-all | 10 | 1/2 | 9/10 | 1.00 |
| no-promotion | 0 | 2/2 | 0/10 | 0.00 |
| no-promotion | 1 | 2/2 | 0/10 | 0.00 |
| no-promotion | 2 | 2/2 | 0/10 | 0.00 |
| no-promotion | 3 | 2/2 | 0/10 | 0.00 |
| no-promotion | 4 | 2/2 | 0/10 | 0.00 |
| no-promotion | 5 | 2/2 | 0/10 | 0.00 |
| no-promotion | 6 | 2/2 | 0/10 | 0.00 |
| no-promotion | 7 | 2/2 | 0/10 | 0.00 |
| no-promotion | 8 | 2/2 | 0/10 | 0.00 |
| no-promotion | 9 | 2/2 | 0/10 | 0.00 |
| no-promotion | 10 | 2/2 | 0/10 | 0.00 |

口径声明：合成反馈是受控信号（规则文本对应探针 needle，噪声文本与种子
知识矛盾），测治理环的传导性与选择性；不是真实用户语料上的效果声明。
governed 臂的「人工甄别」由脚本模拟（正确批准/噪声拒绝）。
