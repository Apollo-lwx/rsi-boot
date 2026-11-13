# tool-choice 误用案例集

借鉴 OpenMausBot `evals/tool-choice`：真实误用案例集，改工具描述/工具清单前后必跑。

## 案例格式（cases.yaml）

```yaml
- id: recall-before-coding
  text: "宿主 agent 面临的场景（用户原话或工作流节点）"
  expect: [rsi_recall]            # 正确工具（必须是已注册 MCP 工具）
  misuse: [直接改代码]             # 真实发生过的误用（rsi_* 形式会校验注册表）
  trigger_terms: {rsi_recall: ["coding"]}  # 描述回归哨兵：锚点词必须在工具实时描述中
  why: "为什么 expect 才对"
  source: "案例出处（教学案例 id / 托管块纪律）"
```

案例全部来自真实误用（本仓库 .rsi/memory 教学案例与 AGENTS.md 托管块纪律），不编造。
起步底线 20 条，当前 22 条。

## 跑法

```bash
python -X utf8 benchmark/tool-choice/check.py            # 离线校验（CI 门禁，tests/test_tool_choice_eval.py 同步值守）
python -X utf8 benchmark/tool-choice/check.py --report   # 追加生成 benchmark/results/tool-choice/SUMMARY.md
rsi eval tool-choice                                     # 同一校验的 CLI 入口
```

## 与 OpenMausBot 的差异

OpenMausBot 用真实模型逐案例跑（`OMB_EVAL_LIVE=1` opt-in）。rsi-boot 本地零 key，
离线层校验「案例结构 × 工具注册表 × 描述锚点词」三方一致；真实模型的选择准确率
留作宿主侧 opt-in 层（未实施，见报告的「不能证明什么」）。
