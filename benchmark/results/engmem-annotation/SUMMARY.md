# EngMem-Bench 三模型标注核验榜 · 2026-11-05

87 题分层样本（type × difficulty，seed 42，覆盖全部 21 仓）由三家模型独立核验：
Kimi K3（OpenAI 兼容端点）、Claude Haiku（Anthropic 兼容中转）、GLM 5.2 Max
（cursor-agent CLI）。协议：逐题对照 gold 锚点节原文裁决
correct / wrong-answer / wrong-anchor / unclear。

## 结果

- 84/87 三家全票一致；Fleiss' κ = 0.738（po=0.973, pe=0.897）
- 逐对 Cohen's κ：kimi↔claude 0.687，kimi↔glm 0.870，claude↔glm 0.686
- 作者仲裁确认 5 处缺陷（均在 fix 切片）：
  - uvicorn-fix-009：锚点 #38→#97（0.50.0 节）
  - starlette-fix-007：锚点 #134→#141（1.4.1 节）
  - celery-fix-001：锚点 #269→#272，问题版本 5.7.0b1→5.7.0a1
  - celery-fix-002：问题版本 5.7.0b1→5.7.0a1（锚点 #361 内容逐字支持答案，
    Claude 判 wrong-anchor 属实——内容挂在 a1 标题下）
  - uvicorn-fix-006：证据反转——面板判 wrong-answer/unclear/wrong-anchor 不一，
    作者复核钉版克隆发现答案真实存在于 0.54.0 的 ### Added 子节，
    终判 wrong-anchor，锚点 #5→#9
- 作者仲裁 vs 各标注者 Cohen's κ：kimi 0.767 / claude 0.808 / glm 0.883

## 产物

- sheet.csv / sheet.instructions.md：导出标注单与说明
- sheet_annotator{,_anthropic,_cursor}.csv + .rationales.jsonl：三家裁决与理由
- sheet_author.csv：作者仲裁表（5 处 wrong-anchor，其余 correct）
- 修复后重跑榜：../engmem-20261105 与 ../engmem-emb-20261105

## 复现

```
python -X utf8 benchmark/engmem/annotation_pack.py --export=sheet.csv
python -X utf8 benchmark/engmem/ai_annotator.py --sheet=sheet.csv                    # kimi
python -X utf8 benchmark/engmem/ai_annotator.py --sheet=sheet.csv --provider=anthropic  # claude
python -X utf8 benchmark/engmem/annotation_pack.py --fleiss=a.csv,b.csv,c.csv
```
