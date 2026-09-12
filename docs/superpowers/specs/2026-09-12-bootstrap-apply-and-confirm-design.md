# Bootstrap 直通 / 整批确认 / 自适应切片 — 设计

> 日期：2026-09-12 | 状态：已定稿，待实现  
> 范围：`rsi bootstrap` 写入策略、切片、报告、冲突扣留、与日常对话学习的隔离  
> 非目标：改召回算法、改 MCP 传输、引入 LLM 做语义对错判断

---

## 1. 问题

1. 一次学习可写出两万多条 `pending_review`，逐条审批不可用；限量 50 + 溢出归档后 `active = 0`，记忆等于没启用。
2. 仓库原文（文档/代码/配置/Git）与「从对话、规则里抽出来的句子」不应走同一闸门。
3. 标题切片过碎或单条过大，既淹检索又拖审批。
4. 多版本文档、文码不一致、前后口径打架时，直通会把冲突一起放进召回。
5. **日常对话学习**（反馈提取、watch 静默学）已有自己的状态机，本次不得用「整批确认」误伤那条链路。

---

## 2. 写入车道

学习结束时每条知识只落在下面三条之一。

| 车道 | 来源 | 写入状态 | 用户动作 |
|------|------|----------|----------|
| A 直通 | 无冲突的仓库原文：文档切片、代码骨架、配置摘要、Git 摘要、由这些推出的干净关联、画像 | `active`（仍走脱敏 + 注入黑名单） | 看报告抽查 |
| B 抽取确认 | 对话抽出的决策/问题、规则文件抽出的禁止项 | `pending_review` | **对话内抉择**（§7.5）；CLI `accept` 仅为无 IDE 兜底 |
| C 冲突确认 | 多版本文档、文码不一致中的文档侧、知识极性不自洽的两侧 | `pending_review`，挂冲突组 | **对话内按组选择题**；禁止无脑双开 |

车道 A 不再经过 `bootstrap.review_queue_cap`。该 cap 只可能作为 B/C 的安全阀（默认 500，超出记警告，不把条目偷偷改成 `archived`）。

每条 bootstrap 产物打标签：`bootstrap_run_id:<uuid>`，外加原有 `signal:*`。整批确认只认这个 run id。

---

## 3. 冲突检测（零 Key，宁缺毋滥）

在写入状态落地前跑启发式。只报有文件信号或极性相反的组，不宣称「读懂业务谁对谁错」。

| 类型 | 信号 | 扣留 |
|------|------|------|
| `version` | 已有：文首 DEPRECATED 并链接现行文档；同目录 `vX.Y` 并排。补：规范化标题相同、路径不同，且正文 Jaccard < 0.85（避免拷贝文件当冲突） | 两版文档条目都扣 |
| `doc_code` | 已有文档↔模块关联上，文档点名的类/函数/接口在骨架里不存在 | 只扣文档；代码骨架仍直通，报告写对照 |
| `incoherent` | 两条知识共享同一 key phrase（≥4 字），窗口极性相反（禁止族 vs 允许族） | 两边都扣 |

复用 `scanner/version_conflict.py` 与 `injector/conflict.py` 的家族/摘录结构，新增 `doc_code` / `incoherent` 行写入 `rule_conflicts`（`conflict_type` 扩展，open 状态）。

裁决（与现网 `rsi_conflicts resolve` 对齐，按组一条决策）：

- `keep-current`：按倾向留新侧或代码现状，另一侧 `archived`
- `keep-legacy`：推翻倾向
- `coexist`：两侧都转 `active`，同组不再打扰

`rsi knowledge accept` **默认不裁决冲突组**，只放行本轮抽取。冲突用：

```text
rsi knowledge accept --conflicts tend
rsi knowledge accept --conflicts coexist
rsi_conflicts resolve   # 单组
```

---

## 4. 自适应切片

替换「有标题就切、上限 4000 token」。

| 参数 | 值 | 作用 |
|------|----|------|
| `target_tokens` | 400 | 一条知识点的目标体量 |
| `min_tokens` | 80 | 再小则与同父级邻居合并 |
| `max_tokens` | 1500 | 硬顶，超出必须再切 |
| 目录条目 | 同一文件子点 ≥ 3 | 另写一条「本文件知识目录」列出子标题，tags 含 `signal:doc-index` |

- 知识面广：按标题成条，碎块合并。
- 体量大：按段落切到目标尺寸，再写目录条目做关联。
- 许可证、纯变更流水、`COPYING` 等：每文件最多一条摘要，不逐行切。
- 校验下限：文档切片仍过噪声门槛；合并后仍低于 `min_tokens` 的丢弃并计入报告「过碎跳过」。

配置挂 `bootstrap.chunk.*`，默认如上。

---

## 5. 报告

每次学习结束写：

- `.rsi/bootstrap_report.md`（给人看，终端打印路径）
- `.rsi/bootstrap_report.json`（机器，沿用并扩展字段）

Markdown 固定六段：

1. 画像（语言/框架/测试/构建；多清单并存时写双栈，禁止「文件数最多的语言 + 先解析到的 Python 框架」这种半对半错）
2. 直通生效：按类型计数 + 每类最多 15 条抽样标题
3. 切片统计：源文件、切出、合并碎块、拆超长、目录条目、过碎跳过
4. 抽取待确认：决策/禁止项标题与来源路径
5. 冲突组：类型、两侧路径/标题、倾向与理由
6. 下一步：打开 Cursor 后下一次任务会在对话里弹出抉择；无 IDE 时才用 CLI（§6）

---

## 6. CLI（兜底，不是主交互）

主交互是宿主对话（§7.5）。CLI 留给没开 IDE、脚本、CI：

```text
rsi bootstrap --consent
rsi bootstrap --force --consent
rsi knowledge accept                 # 无对话时放行本轮抽取
rsi knowledge accept --reject
rsi knowledge accept --conflicts tend|coexist
rsi knowledge accept --run <uuid>
```

`rsi_knowledge_review` 的 `all_pending` **不得**把库里所有 pending 一次放行。没带过滤时只处理 `auto-extract`；bootstrap 批次走对话抉择或 `accept`。

---

## 7. 日常对话学习：不受本次「整批确认」误伤

日常有三条入口，与 bootstrap 共用 `knowledge_items` 表，但**标签、来源、确认命令必须分开**。

### 7.1 现状（保持）

| 入口 | 现在怎么写 | 确认 |
|------|------------|------|
| `rsi_feedback` → 每日 `KnowledgeExtractor` | `source_url='auto-extract'`，`pending_review` | 对话内抉择（§7.5）；MCP `rsi_knowledge_review` 仍可用 |
| 标题完全相同 / 增强层 cosine≥0.9 | 合并到已有 active/pending，不新开一条 | 无 |
| `rejected` / `suppressed` | 不复活、不重写 | 无 |
| `rsi serve --watch` 文档变更 | 同 source 重切片，**直写 `active`**（文件就是该 source 的新原文） | 无 |
| 每周 `ConflictDetector.scan` | 只把 **已 active** 的 prohibition/convention/experience 和**用户手写规则文件**比对 | 对话内抉择 + `rsi_conflicts` |

### 7.2 隔离硬规则（本次必须落地，防回归）

1. `rsi knowledge accept` 的 WHERE 必须含 `bootstrap_run_id`（或显式 `--run`）。`source_url = 'auto-extract'` 的行**一律不碰**。
2. `bootstrap.review_queue_cap` 与溢出改 `archived` **只扫描带 `bootstrap_run_id` 的行**。日常待审草稿不得被 cap 挤进归档。
3. `reconcile_scope`（按 `signal:code|config|git|correlation|rules` 收敛旧摘要）不得匹配 `auto-extract` / `incremental` 以外的手写条目。日常经验没有这些聚合标签。
4. 不改变提取器默认：日常草稿仍 `pending_review`，不因「仓库原文直通」变成 active。
5. `rsi_knowledge_review all_pending`：只处理 `source_url='auto-extract'` 或无 `bootstrap_run_id` 的 pending。避免宿主模型一次「全部审批」把未看的 bootstrap 冲突组放行。

### 7.3 日常新知识 vs 历史知识冲突（补强，但不改成 bootstrap 整批）

日常量小（每日提取上限约 50 候选），不走 `rsi knowledge accept`。

落库前顺序：

1. 标题/向量去重 → 仍算「同一条」，合并，**不开冲突**。
2. 否则插入 `pending_review`。
3. 用与 bootstrap 相同的 `incoherent` 启发式，对 **已 active** 历史条目做 key phrase + 极性检查。
4. 若命中：历史条目**保持 `active`**（已经生效过，不能因为一条未审草稿就下线）；新草稿保持 `pending_review`；写入一条 open 冲突（`conflict_type=incoherent`，`item_id` 指向新稿，摘录里带上历史条目 id）。
5. **不要求用户去终端敲命令。** 下一次 `rsi_recall` 带上结构化 `decisions`，宿主在对话里列出选项，用户用自然语言选完，宿主调用 `rsi_conflicts resolve`（见 §7.5）。
6. 裁决语义不变：新稿胜则归档历史并激活新稿；旧稿胜则拒绝新稿；`coexist` 则只激活新稿、两边都留。

### 7.4 watch 静默学

本次**不改** watch 语义：同一 `source_url` 文件变了，仍直写 `active` 并收敛该 source 的旧切片。跨文件冲突等下次 bootstrap 或每周扫描再报。不把 watch 增量塞进 bootstrap 的整批清单。

### 7.5 对话内抉择（主交互）

执行方是**宿主 agent**，不是用户。Cursor 里 agent 本来就会调 MCP；用户习惯也是「你直接帮我做」。因此：

- **必须由 agent 自己调用** `rsi_conflicts` / `rsi_knowledge_review`（或它自己在终端跑 `rsi …`）。
- **禁止**只回一句「请你自己去运行 rsi …」然后停手——那是把作业甩给用户，不是禁止 agent 跑命令。
- 有明确选择就按选择调工具；用户说「你看着办 / 自动执行 / 帮我处理」时，按卡片上的 `recommended` 立刻调用，不必再追问。

RSI 不能在 IDE 里画按钮，但可以出选择题；**落库动作一律走工具调用**。

**召回契约。** `rsi_recall` 增加 `decisions`（可空）：

```json
{
  "decisions": [{
    "id": "冲突或抽取批次 id",
    "kind": "daily_conflict | bootstrap_extract | bootstrap_conflict",
    "prompt": "新对话里出现「禁止用 pydantic」，和已生效的「API 用 pydantic」打架。你要哪边？",
    "options": [
      {"id": "keep_item", "label": "用新的（对话里刚确认的）", "resolution": "keep_item"},
      {"id": "keep_peer", "label": "维持原来的", "resolution": "keep_peer"},
      {"id": "coexist", "label": "两版都留，以后按场景再看", "resolution": "coexist"}
    ],
    "recommended": "keep_item",
    "recommended_reason": "新稿来自刚才这次对话的明确否定",
    "sides": [
      {"role": "new", "title": "禁止：pydantic", "excerpt": "……", "source": "auto-extract"},
      {"role": "old", "title": "API 用 pydantic", "excerpt": "……", "source": "docs/api.md"}
    ],
    "impact": {
      "recall": "之后相关任务会按你选的那条注入",
      "inject": "规则文件 rsi-*.mdc / AGENTS.md 会改哪一侧",
      "code_hint": "骨架/文档里点到的路径，没有则空"
    },
    "more_waiting": 2
  }],
  "prohibitions": [],
  "items": []
}
```

- 每次召回最多带 **1** 张抉择卡，避免劫持任务。`more_waiting` 告诉宿主还有几张排队。
- 优先级：`daily_conflict` > `bootstrap_conflict` > `bootstrap_extract`（日常对话冲突最先问）。
- 抽取批 `bootstrap_extract` 的选项是「本轮抽取全部生效 / 全部丢掉 / 先跳过」，不是逐条。
- 冲突卡的 `options.id` 必须能直接传给 `rsi_conflicts resolve` 的 `resolution`。
- `sides` / `impact` 是给 agent **展开说明**用的，默认提问只用 `prompt` + `label`，避免第一次就倒一长篇。

**对话状态（同一张卡可以多轮）：**

| 用户意图 | 判定 | agent 做什么 | 卡还在吗 |
|----------|------|--------------|----------|
| 选了某个 option / 对应自然语言 | 裁决 | 调 `resolve`，回报结果 | 关闭 |
| 「你看着办 / 自动执行」 | 裁决 | 用 `recommended` 调工具 | 关闭 |
| 「展开 / 没看懂 / 详细说说 / 举个例子」 | **追问，不是跳过** | 用 `sides` 讲清楚两边；不够再 `rsi_conflicts` `action=explain` | **仍打开**，说完再列同一组选项 |
| 「影响面 / 会改哪些文件 / 对召回有什么影响」 | **追问** | 用 `impact` + explain 的影响表回答，再列同一组选项 | **仍打开** |
| 「为什么推荐这个」 | **追问** | 解释 `recommended_reason` | **仍打开** |
| 「跳过 / 待会再说」 | 推迟 | 不 resolve | 本会话抑制 24h |
| 没表态，改去写代码 | 搁置 | 不 resolve、不抑制 | 下次新任务再出这张 |

追问轮**禁止**误判成 skip，也**禁止**还没选就 resolve。同一 MCP 进程里，已出示但未关闭的卡优先于队列里的下一张（sticky）：用户说「那影响面呢」时，即使 agent 又调了一次 `rsi_recall`，仍返回同一 `id`。

`rsi_conflicts` 增加 `action=explain`（`conflict_id` 必填），返回两侧较全文摘、来源、选每个 option 之后 status/注入会怎么变。纯说明，不改库。

**工具描述（改 `rsi_recall` / `rsi_conflicts` 文案，驱动宿主）：**

- `decisions` 非空时：用 `prompt` + `label` 问一句（或用户已说自动执行则不问）。
- **agent 自己调用** `rsi_conflicts resolve` / `rsi_knowledge_review` 写入结果。优先 MCP；没有 MCP 再用自己开的 shell 跑等价 CLI。不要让用户去复制命令。
- 用户说「你看着办 / 自动执行 / 按推荐」：立刻用 `recommended` 对应的 `resolution` 调工具，然后用一句话回报结果。
- 用户要展开或问影响面：先 `explain` 或用卡上的 `sides`/`impact` 答完，**同一组选项再问一次**。
- 用户说「跳过 / 待会再说」：本会话不再重复这张卡（同一 `id` 抑制到进程重启或 24h）。
- 用户没表态就继续改代码：不阻断；下次新任务的 recall 再出卡。

**注入规则。** 常驻短指令：有 `decisions` 时由你调 RSI 工具落库；用户要你自动执行就用 `recommended`；用户要展开就解释，不要关掉这张卡。冲突正文不要当禁止项注入。

**无 IDE。** 纯终端场景才需要人自己敲 `rsi knowledge accept`。有 agent 的对话里这是 agent 的活。

---

## 8. 画像

`scan_configs` 禁止「先解析到的框架锁死」。多份清单并存时：

- `language`：按代码扩展名占比；≥40% 可并列写（如 `java,python`），不再只留一个。
- `framework` / `test_framework`：按语言分桶收集，报告写成 `python:pydantic+pytest; java:maven+junit` 这种可空分桶，而不是 `language=java, framework=pydantic`。

此项随 bootstrap 报告一起做，避免直通配置摘要继续写错栈。

---

## 9. 已有项目迁移

`db-grammar-auto-adaptor` 这类「已 bootstrap、文档全在 archived、active=0」的库，**不自动提升**。用户二选一：

1. 删项目 `.rsi/rsi.db*` 与 `manifest.json` 后按新规则重学；
2. 实现后可用 `rsi knowledge accept --promote-archived-repo --run <old>` **不在第一期做**。第一期文档只说明清库重学。

`--force` 仍不把相同内容哈希的 archived 再入队（去重语义不变）。

---

## 10. 错误与边界

- 脱敏/黑名单失败：该条跳过，计入报告错误，不阻断整次学习。
- 冲突检测 0 组：车道 C 为空，报告写「未发现需裁决的冲突」。
- 无抽取：`accept` 打印「本轮无抽取项」退出 0。
- `--strict`：校验失败仍中断（沿用现网）。
- dry-run：不写库。报告含信号发现 + 将采集维度 + 「将直通 / 将进确认」的文件级预估；不跑自适应切片，不跑冲突启发式。

---

## 11. 测试要点

- 小仓库：干净文档+配置 → 条目 `active`，pending 仅为 0 或抽取。
- 版本并排 `foo-v1.0.md` / `foo-v1.1.md` → 两版 pending，直通计数不含它们。
- 文档点名不存在的 `class Missing` → 该文档 pending，代码骨架 active。
- 极性相反两条 convention → 都 pending，开 `incoherent`。
- `rsi knowledge accept` 后抽取变 active；同库预置 `source_url=auto-extract` 的 pending **状态不变**。
- 将 `review_queue_cap=2`，插入 10 条 auto-extract pending，跑 bootstrap → 这 10 条仍 pending。
- 切片：无标题 6000 token 正文 → 多条 ≤1500 + 一条目录；十个 20 token 小节 → 合并后条数远小于 10。
- 画像：根 `pyproject.toml`（pydantic）+ `pom.xml`（junit）+ 更多 `.java` → 报告双栈，不再 `framework=pydantic` 单独配 `language=java`。
- 提取器：新 prohibition 草稿与已 active「允许 X」→ 新稿 pending，历史仍 active，存在 open 冲突。
- `rsi_recall` 在存在 daily_conflict 时 `data.decisions[0].kind == daily_conflict`，且 `options` 可映射到 resolve；同库 `auto-extract` pending 不会因 `accept` 被放行。
- 召回工具描述要求 agent 自行调用 resolve/review，并支持 `recommended` 自动执行；不把命令甩给用户手敲。
- 「展开说明」后同一 `decision.id` 仍 open；`explain` 不改库；误把「没看懂」当成 skip 的用例必须失败。

---

## 12. 模块边界

| 单元 | 职责 |
|------|------|
| `scanner/document_scanner.py` | 自适应切 + 目录条目 |
| `scanner/conflict_gate.py`（新） | 对拟写入条目分组：直通 / 扣留；产出冲突草稿 |
| `cli/bootstrap_command.py` | 按组写 status、打 run id、取消对 A 的 cap |
| `scanner/report.py` | Markdown 报告 |
| `cli/knowledge_accept.py`（新）或 `__main__.py` | 按 run id 整批抽取/冲突 |
| `learning/knowledge_extractor.py` | 落库后调用同一 `incoherent` 检查；不改默认 status |
| `api/tools/recall_tool.py` + recall 服务 | 载荷增加 `decisions`；工具描述要求宿主先提问再 resolve |
| `injector` 召回规则槽 | 常驻「有 decisions 先问用户，禁止甩 CLI」 |
| `services/knowledge_service.py` | `review_batch` 增加 `bootstrap_run_id` / `source` 过滤；`all_pending` 排除 bootstrap run |
| `scanner/config_scanner.py` + `profile_generator.py` | 分桶画像 |

不改 MCP 工具名。`rsi_conflicts` 增加列出 `doc_code`/`incoherent`；resolve 动词与 version 组相同的 keep-* / coexist。
