# 可读记忆层（YAML SoT + RAG + 学习 DAG）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作区记忆从 `rsi.db` 换成 `.rsi/` 文件树；serve 零 sqlite；召回/注入/反馈/教学按规格接线；叙述跟调用方 zh/en，SQL/代码/工具名/术语始终原样。

**Architecture:** 先落地文案常量与进度钩子，再做 MemoryDoc/YAML/JSONL，然后注入与召回以 `store=` 可选方式改读文件（Runtime 仍 sqlite）。migrate 一次卸完旧库。11a 给冲突/统计/提案换文件后端，11b 才切断 `build_runtime` 的 sqlite。P1/P2 在 P0 可 ship 后再加切片召回、审计/收工、晋升与收工注入块。通道目录独占与单 SoT 是硬约束。

**Tech Stack:** Python 3.12、pytest、Pydantic、PyYAML（`safe_load` / `safe_dump`）、现有 `cli/progress.py`、MCP stdio。Windows 测试一律 `python -X utf8 -m pytest …`；PowerShell 不用 `&&`。

**Spec:** `docs/superpowers/specs/2026-09-13-readable-memory-rag-dag-design.md`

## Global Constraints

- 运行时单 SoT：`rsi serve` / `--then-start` 不得调用 `SQLiteClient.connect`。唯一 `connect` 入口是 `rsi memory migrate`。
- 禁止双写：不允许召回读文件、注入读表并行。
- 写入通道独占见规格 §2.3。测试用路径前缀断言。
- 注入：alwaysApply 只正式目录 prohibition；不生成 `rsi-convention-*.mdc`。收工纪律块 P2 才写入 AGENTS。
- `retrieved` 新行必须是文档 id 列表。
- TDD：先红后绿。提交仅在用户要求或执行本计划时按步提交。
- 零 Key 主链路：意图规则 + 倒排 + CJK bigram；有 embedding 再加向量。
- **检索：** 用户/模型原文走意图 + RAG。禁止 `title == task` / 签名全等作为命中条件。按 id 打开除外。晋升计数仍用精确 `error_signature + failure_type`。
- 不接线：`Pipeline` / `rsi_query` / `SkillLoader.match`。
- **交互：** 叙述用 `t(key, lang)` 跟调用方走。SQL、代码、路径、工具名、`state/terms.yaml` 里的术语**原样代入**，不译。记忆文件的中文说明保持中文，其中的英文片段也保持英文。`list_tools` 叙述中英并列。禁止工具函数里手写散句。
- **存量测试：** Task 11b 结束时 `python -X utf8 -m pytest tests` 必须全绿。`conftest.py` 与仍构造 `SQLiteClient` 的用例，在 11b 改成 `MemoryStore` 或标为 migrate 专用。禁止留下「最小适配」未改的红测。

---

## 复审锁定（2026-09-13）

- 运行时短句一律 `t("KEY", lang)`，禁止再引用 `m.MIGRATE_NO_DB` 这种单语常量。
- `expand_query` 必须带同义表，否则口语/英文 paraphrase 测例会永久红。内置至少：`星号/所有列/* /star` ↔ `SELECT`/`列名`。
- Task 6–10 的新模块用 `MemoryStore` **单测**，不改 `build_runtime`。切 Runtime 只发生在 Task 11b。
- Task 6–10 改现网服务时一律加 `store=` 可选参数：有 store 走文件，无 store 保持旧 db。禁止在 11b 之前删除 db 分支或宣称「只有 migrate 能 import sqlite」。
- `RuleInjector` / `RecallService` / `KnowledgeService` 都遵守上一条，避免中途弄红 `tests/test_injector.py`、`tests/test_recall.py`。
- 脱敏（redact-not-drop）在 Task 3 `write` 落地。
- `knowledge_search` 与 `rsi_recall.items` 共用 `expand_query`，不得精确比标题。
- `rsi_conflicts` / `rsi_stats` / `rsi_review` / `implicit_tracker` 按文档 id 归因；文件后端在 Task 11a，Runtime 换绑在 11b。
- 与规格对照：P0 召回不含 gene/teaching/episode；manual 文件名 `{slug}--{id8}.yaml`；`store.read`；禁止项超 32KB 按采纳、无采纳按 `updated_at` DESC；JSONL 超 50MB 滚动；schema 失败写 `logs/errors.jsonl`。

---

## 交互契约（文案与进度，全任务必须遵守）

叙述可本地化；符号与术语始终英文。见规格 §4.11。

### 语言怎么定

```python
# ux/lang.py
def preserve_spans(text: str, extra_terms: list[str] | None = None) -> str:
    """返回去掉原样片段后的文本，供探测。不改调用方的原文。"""

def detect_lang(*texts: str, explicit: str | None = None) -> Literal["zh", "en"]:
    """先 preserve_spans，再：explicit → han>=2 则 zh → 有字母则 en → 进程上次 → OS locale → en。"""
```

原样片段（写入与 `t()` 都不得改写）：反引号块、路径、`rsi_*`、JSON/schema 键、SQL 关键字与语句、全大写标识、`.rsi/state/terms.yaml` 中的词。

| 表面 | 规则 |
|------|------|
| YAML / JSONL / MD / 禁止项正文 | 中文**叙述**保持中文；其中的 SQL/代码/术语保持英文。整篇不做机器翻译 |
| MCP `list_tools`、instructions | 叙述中英并列；工具名只写英文一次 |
| MCP `message` / `hint` / 错误句 | `t(key, lang, path=…, tool=…)`；占位符原样 |
| CLI | `--lang` / `RSI_LANG` / 探测；阶段名可译，子命令与路径不译 |
| 召回 `items[].content` | 磁盘原文（中文叙述 + 英文片段），不因 `message` 是英文而改写 |

进程内记住上次 MCP 探测结果，供无文本的 follow-up（如只带 id 的 `teach_record`）使用。

### 通道

| 通道 | 规则 |
|------|------|
| CLI 进度 / 成功摘要 | stdout；TTY 用 `Progress` 的 `\\r` 刷新；管道打整行 |
| CLI 错误 | stderr；一句本地化短句；必要时第二行写路径或 `rsi …` 下一步 |
| CLI 机器输出 | 仅 `--json` 时 stdout 纯 JSON，进度改走 stderr |
| MCP | JSON 信封；`status=success\|error`；成功可带一行 `message`；错误必带 `code` + `message` |
| MCP 日志 | serve 默认 WARNING；禁止把进度打到 MCP stderr |

### MCP 信封

```python
{"status": "success", "message": t("TEACH_RECORDED", lang, path=path), "data": {…}}
{"status": "error", "code": "not_in_phase"|"invalid"|"not_found"|"schema"|"conflict",
 "message": t("PHASE_PROMOTE", lang)}
```

`code` 稳定英文。`message` 必须告诉宿主下一步调哪个工具（工具名不翻译）。

### CLI exit

| 码 | 含义 |
|----|------|
| 0 | 成功（含 dry-run 预览成功） |
| 2 | 用法错误（缺参数、互斥旗标） |
| 3 | 业务失败（无旧库、schema 失败、未找到 id） |
| 1 | 未预期异常（stderr 仍本地化短句，不打印栈；`--verbose` 才打栈） |

### 词条目录（`ux/messages.py`：每个 key 都有 `zh` 与 `en`）

`t(key, lang, **kwargs)` 格式化。测试断言两边都非空，且 en 不含汉字、zh 含至少一个汉字（`RECALL_DESC` 等并列描述除外）。

```python
STRINGS = {
    "MIGRATE_TITLE": {"zh": "开始迁出记忆：{root}", "en": "Migrating memory: {root}"},
    "MIGRATE_PHASE_OPEN": {"zh": "打开旧库", "en": "Open legacy database"},
    "MIGRATE_PHASE_KNOWLEDGE": {"zh": "导出知识", "en": "Export knowledge"},
    "MIGRATE_PHASE_LOGS": {"zh": "导出日志", "en": "Export logs"},
    "MIGRATE_PHASE_STATE": {"zh": "导出状态", "en": "Export state"},
    "MIGRATE_PHASE_WRITE": {"zh": "写入文件", "en": "Write files"},
    "MIGRATE_PHASE_BAK": {"zh": "备份旧库", "en": "Rename legacy database"},
    "MIGRATE_PHASE_INDEX": {"zh": "建立索引", "en": "Build index"},
    "MIGRATE_PHASE_INJECT": {"zh": "注入禁止项", "en": "Inject prohibitions"},
    "MIGRATE_DONE": {"zh": "迁出完成", "en": "Migration complete"},
    "MIGRATE_DONE_DRY": {"zh": "（仅预览，未改磁盘）", "en": "(dry-run, nothing written)"},
    "MIGRATE_NO_DB": {
        "zh": "没有找到可迁出的 rsi.db。当前目录已是文件记忆，或尚未初始化。",
        "en": "No rsi.db to migrate. This workspace already uses file memory, or is not initialized.",
    },
    "MIGRATE_HALF": {
        "zh": "迁出未完成，已停止。未改名 rsi.db。查看 .rsi/logs/errors.jsonl",
        "en": "Migration stopped. rsi.db was not renamed. See .rsi/logs/errors.jsonl",
    },
    "MIGRATE_SUMMARY": {
        "zh": "知识 {knowledge} 条，日志 {logs} 行，臂 {arms} 条，未映射 retrieved {unmapped} 条。旧库已改名为 {bak}",
        "en": "{knowledge} knowledge files, {logs} log lines, {arms} arms, {unmapped} unmapped retrieved. Legacy DB renamed to {bak}",
    },
    "REINDEX_TITLE": {"zh": "重建索引：{root}", "en": "Rebuilding index: {root}"},
    "REINDEX_DONE": {"zh": "索引已重建", "en": "Index rebuilt"},
    "BOOTSTRAP_DONE": {"zh": "学习完成", "en": "Learning complete"},
    "DURATION": {"zh": "总耗时 {duration}", "en": "elapsed {duration}"},
    "MEMORY_NEED_SUB": {
        "zh": "请指定子命令：migrate / reindex / index / open / graph",
        "en": "Specify a subcommand: migrate / reindex / index / open / graph",
    },
    "LEARN_NEED_ACTION": {
        "zh": "请指定动作：teach_catch / teach_record / skip / rubric（其余动作按期开放）",
        "en": "Specify an action: teach_catch / teach_record / skip / rubric (other actions come later)",
    },
    "TEACH_RECORDED": {"zh": "已写入教学案例 {path}", "en": "Wrote teaching case {path}"},
    "TEACH_CATCH_SAVED": {
        "zh": "已记下现场 {id}，写好 lesson 后调用 teach_record",
        "en": "Saved catch {id}. Call teach_record after you write the lesson.",
    },
    "REVIEW_APPROVED": {
        "zh": "已获准 {n} 条，禁止项已注入规则文件",
        "en": "Approved {n} item(s). Prohibitions were injected.",
    },
    "REVIEW_REJECTED": {"zh": "已归档 {n} 条", "en": "Archived {n} item(s)"},
    "EXTRACT_CLOSEOUT": {
        "zh": "本轮学习清单如下，请在最终总结中照抄路径或写跳过原因",
        "en": "Closeout list below. Copy the paths into your final summary, or write why you skipped.",
    },
    "PHASE_PROMOTE": {
        "zh": "promote 下一期才开放。规范转正请用 rsi_knowledge_review。",
        "en": "promote is not available yet. Approve pending norms with rsi_knowledge_review.",
    },
    "PHASE_GRAPH": {
        "zh": "graph 下一期才开放。先用 rsi_memory action=index 看目录。",
        "en": "graph is not available yet. List files with rsi_memory action=index.",
    },
    "PHASE_AUDIT": {
        "zh": "审计动作下一期才开放。本轮请用 teach_record 记录可复用修法。",
        "en": "Audit actions are not available yet. Use teach_record for reusable fixes this wave.",
    },
    "HINT_TEACH": {
        "zh": "若本轮修失败或用户纠正，先 rsi_learn teach_catch",
        "en": "If this repair fails or the user corrects you, call rsi_learn teach_catch first.",
    },
    "SEC": {"zh": "{n}秒", "en": "{n}s"},
    "MIN": {"zh": "{n}分", "en": "{n}m"},
    "HOUR": {"zh": "{n}小时", "en": "{n}h"},
    "NOT_FOUND": {"zh": "未找到记忆 {id}", "en": "Memory {id} not found"},
    "YAML_INVALID": {"zh": "YAML 字段不合法：{path}", "en": "Invalid YAML fields: {path}"},
    "WIPE_HINT": {
        "zh": "清掉本项目 .rsi 下的记忆文件与缓存，保留 identity.json。确认请加 --yes。",
        "en": "This deletes memory files and cache under .rsi, and keeps identity.json. Pass --yes to confirm.",
    },
    "WIPE_DONE": {"zh": "已删除记忆文件", "en": "Deleted memory files"},
    "WIPE_KEPT": {"zh": "已保留: {path}", "en": "Kept: {path}"},
    "BOOTSTRAP_PHASE_INIT": {"zh": "初始化记忆目录", "en": "Initialize memory directories"},
}

# list_tools：中英并列，不走 t()。改字必须同步测试。
TOOL_DESC = {
    "recall": (
        "Call before any coding, adaptation, debugging, or design task. "
        "Returns prohibitions you must follow, related items, and a skill catalog "
        "(name/description/path only). Use feedback_token with rsi_feedback before you wrap up. "
        "If decisions is non-empty, call rsi_conflicts / rsi_knowledge_review immediately; "
        "do not ask the user to run rsi in a terminal. "
        "任务开始前调用。返回必须遵守的 prohibitions、相关 items，以及技能目录。"
        "用 feedback_token 在收工前调用 rsi_feedback。"
        "有 decisions 时立刻调 rsi_conflicts / rsi_knowledge_review，不要让用户去终端跑 rsi。"
    ),
    "learn": (
        "Teaching / audit / closeout learn / promote. The host agent writes the lesson "
        "(Catch→Teach→Fix) then teach_record; RSI does not write the lesson. "
        "Unavailable actions return not_in_phase — follow message. "
        "教学/审计/收工学习/晋升。Agent 自己 Catch→Teach→Fix 后 teach_record；"
        "不要让 RSI 代写 lesson。未开放的 action 返回 not_in_phase，按 message 改用其它工具。"
    ),
    "memory": (
        "Open or search memory files. index lists entries, open reads one id, reindex rebuilds cache. "
        "graph comes later. "
        "打开或检索记忆文件。index 列目录，open 按 id 读全文，reindex 重建缓存。graph 下一期才开放。"
    ),
    "review": (
        "Approve pending norms: approve moves to official dirs and injects prohibitions; "
        "reject archives. Does not create new bodies. Approved conventions are recall-only "
        "(no rsi-convention rule files). "
        "审批 pending 规范：approve 搬到正式目录并注入禁止项，reject 搬到 archive。"
        "不新建正文。约定获准后只进召回，不会写成 rsi-convention 规则文件。"
    ),
}
```

`format_duration` 的「秒/分/小时」同样走 `t("SEC"|"MIN"|"HOUR", lang)`，避免英文进度里夹「秒」。

### CLI 进度阶段（migrate / reindex）

复用 `Progress`。`finish` 支持自定义 `summary`；迁出结束用 `t("MIGRATE_DONE", lang)`，**禁止**在 `lang=en` 时打印「学习完成」。bootstrap 默认 `t("BOOTSTRAP_DONE", lang)`。

```python
def finish(self, message: str = "", *, summary: str | None = None, lang: str | None = None) -> None:
    from rsi_boot.ux.lang import locale_lang
    from rsi_boot.ux.messages import t
    loc = lang or locale_lang()
    head = summary if summary is not None else t("BOOTSTRAP_DONE", loc)
    extra = f"  {message}" if message else ""
    self._writeln(f"{head} {t('DURATION', loc, duration=format_duration(self._total_elapsed(), loc))}{extra}")
```

`format_duration` 增加 `lang`，内部用 `t("SEC"|"MIN"|"HOUR", lang, n=…)`。中文环境缺省摘要仍是「学习完成」。

migrate：`progress.start(8, title=t("MIGRATE_TITLE", lang, root=...))`，阶段名 `t("MIGRATE_PHASE_*", lang)`。`--dry-run`：`finish(t("MIGRATE_DONE_DRY", lang), summary=t("MIGRATE_DONE", lang))`。`--json` 时 `Progress(stream=sys.stderr)`。

reindex：3 段 scan/index/cache，`summary=t("REINDEX_DONE", lang)`。

### 宿主可见返回（MCP `data` 最低字段）

- `rsi_recall`：`prohibitions` / `items` / `skills` / `feedback_token`（正文中文原文）；P1 `hint=t("HINT_TEACH", lang)`
- `rsi_learn teach_record`：`path` / `id` / `manual_path` / `closeout`（`one_liner` 可来自记忆中文标题，外层 `message` 本地化）
- `rsi_knowledge_review`：`moved: [{id, from, to}]` / `injected: bool`
- 任何写盘成功：至少一条相对工作区的 POSIX 路径（路径不翻译）

---

## File map

| File | Responsibility |
|------|----------------|
| `src/rsi_boot/ux/lang.py` | `detect_lang` / `preserve_spans` / `locale_lang` |
| `src/rsi_boot/ux/terms.py` | 内置原样词 + 合并 `state/terms.yaml` |
| `src/rsi_boot/ux/messages.py` | `STRINGS` zh/en + `t()` + `TOOL_DESC` 中英并列 |
| `src/rsi_boot/ux/__init__.py` | 导出 |
| `src/rsi_boot/cli/progress.py` | `finish(..., summary=)` |
| `src/rsi_boot/memory/types.py` | `MemoryDoc`、type 枚举、目录↔status |
| `src/rsi_boot/memory/store.py` | YAML 读写、原子 replace、同 id 教学对 |
| `src/rsi_boot/memory/logstore.py` | JSONL append / 扫描 / 滚动 |
| `src/rsi_boot/memory/paths.py` | `.rsi/memory` 约定路径 |
| `src/rsi_boot/memory/watch.py` | 只监 `memory/**/*.yaml` |
| `src/rsi_boot/rag/query.py` | `expand_query`：意图词 + 原样 SQL + 同义；禁止精确标题短路 |
| `src/rsi_boot/rag/index.py` | P0 整篇倒排 + CJK；写 `cache/` |
| `src/rsi_boot/rag/retriever.py` | 扩展查询打分 + 意图加权；P1 再切片 |
| `src/rsi_boot/rag/chunker.py` | P1 |
| `src/rsi_boot/learning/teaching.py` | teach_catch / teach_record |
| `src/rsi_boot/learning/pipeline.py` | 反馈提取 → pending 规范 |
| `src/rsi_boot/learning/gene_map.py` | P1 extract |
| `src/rsi_boot/learning/audit_store.py` | P1 |
| `src/rsi_boot/learning/promote.py` | P2 |
| `src/rsi_boot/services/memory_migrate.py` | 只读旧库 → 文件 |
| `src/rsi_boot/services/recall_service.py` | 改读文件；`retrieved` 为 id |
| `src/rsi_boot/services/knowledge_service.py` | 改读写 YAML |
| `src/rsi_boot/injector/rule_injector.py` | 读正式 prohibition YAML |
| `src/rsi_boot/injector/targets.py` | 决策块文案；P2 收工块 |
| `src/rsi_boot/bootstrap.py` | Runtime 无 db；启动 inject |
| `src/rsi_boot/__main__.py` | `memory` / `learn` 子命令 |
| `src/rsi_boot/cli/memory_command.py` | migrate/reindex/index/open |
| `src/rsi_boot/cli/learn_command.py` | 与 MCP 同语义 |
| `src/rsi_boot/api/tools/learn_tool.py` | `rsi_learn` |
| `src/rsi_boot/api/tools/memory_tool.py` | `rsi_memory` |
| `src/rsi_boot/api/mcp_server.py` | 登记 11 工具；instructions |
| `src/rsi_boot/cli/wipe_command.py` | 清 yaml 树，文案改「文件记忆」 |
| `README.md` | 迁出与 serve 说明 |
| `tests/test_ux_messages.py` | 文案常量存在且无空串 |
| `tests/test_progress_summary.py` | finish summary |
| `tests/test_memory_store.py` | schema / 原子写 / 目录 status |
| `tests/test_logstore.py` | append / retrieved id |
| `tests/test_rag_index.py` | CJK 倒排 |
| `tests/test_inject_yaml.py` | 无 convention mdc |
| `tests/test_recall_files.py` | retrieved 为 id |
| `tests/test_memory_migrate.py` | 卸库 + 进度阶段名 |
| `tests/test_runtime_no_sqlite.py` | serve 不 connect |
| `tests/test_learn_tool.py` | teach + not_in_phase 文案 |
| `tests/test_cli_memory.py` | exit 码与 stderr 句 |

不修改：`orchestrator/pipeline.py`、`api/tools/query_tool.py`（不登记即可）。

---

### Task 1: 文案常量 + Progress 摘要

**Files:**
- Create: `src/rsi_boot/ux/__init__.py`
- Create: `src/rsi_boot/ux/lang.py`
- Create: `src/rsi_boot/ux/terms.py`
- Create: `src/rsi_boot/ux/messages.py`
- Modify: `src/rsi_boot/cli/progress.py`（`finish` 的 `summary`）
- Test: `tests/test_ux_lang.py`、`tests/test_ux_messages.py`、`tests/test_progress_summary.py`

**Interfaces:**
- Consumes: 现有 `Progress.finish(message="")`。
- Produces: `detect_lang`、`t(key, lang, **kwargs)`、`TOOL_DESC`；`Progress.finish(message="", *, summary=None, lang=None)`，`summary` 缺省 `t("BOOTSTRAP_DONE", locale_lang())`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ux_lang.py
from rsi_boot.ux.lang import detect_lang

def test_detect_zh_from_han():
    assert detect_lang("修一下查询") == "zh"
    assert detect_lang("Fix the 禁止项 now") == "zh"
    assert detect_lang("修一下 SELECT * FROM t") == "zh"

def test_detect_en_from_letters():
    assert detect_lang("Fix the SQL query") == "en"

def test_preserve_sql_not_translated():
    from rsi_boot.ux.messages import t
    msg = t("TEACH_RECORDED", "zh", path="memory/x.yaml")
    assert "memory/x.yaml" in msg

def test_explicit_wins():
    assert detect_lang("修一下", explicit="en") == "en"
```

```python
# tests/test_ux_messages.py
from rsi_boot.ux.messages import STRINGS, TOOL_DESC, t

def test_keys_have_zh_and_en():
    for key, pair in STRINGS.items():
        assert pair["zh"].strip() and pair["en"].strip(), key
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["zh"]
    assert "rsi_knowledge_review" in STRINGS["PHASE_PROMOTE"]["en"]

def test_t_en_has_no_han():
    assert not any("\u4e00" <= c <= "\u9fff" for c in t("MIGRATE_DONE", "en"))

def test_tool_desc_bilingual():
    assert "Call before" in TOOL_DESC["recall"]
    assert "任务开始前" in TOOL_DESC["recall"]
    assert "rsi_feedback" in TOOL_DESC["recall"]
```

```python
# tests/test_progress_summary.py
from io import StringIO
from rsi_boot.cli.progress import Progress
from rsi_boot.ux.messages import t

def test_finish_custom_summary():
    buf = StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(1, title="t")
    p.phase(t("MIGRATE_PHASE_KNOWLEDGE", "zh"), total=1)
    p.tick(1)
    p.finish(t("MIGRATE_HALF", "zh")[:8], summary=t("MIGRATE_DONE", "zh"))
    text = buf.getvalue()
    assert "迁出完成" in text
    assert "学习完成" not in text

def test_finish_en_migrate_not_chinese_done():
    buf = StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(1)
    p.phase(t("MIGRATE_PHASE_KNOWLEDGE", "en"), total=1)
    p.tick(1)
    p.finish(summary=t("MIGRATE_DONE", "en"))
    assert "Migration complete" in buf.getvalue()
    assert "学习完成" not in buf.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -X utf8 -m pytest tests/test_ux_lang.py tests/test_ux_messages.py tests/test_progress_summary.py -v`

Expected: FAIL（模块不存在或 `finish` 没有 `summary`）

- [ ] **Step 3: Write minimal implementation**

`lang.py` + `STRINGS`/`TOOL_DESC`/`t()` 按本计划粘贴。`Progress.finish` 增加 `summary`；缺省 `t("BOOTSTRAP_DONE", locale_lang())`，中文环境与今日「学习完成」一致。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -X utf8 -m pytest tests/test_ux_lang.py tests/test_ux_messages.py tests/test_progress_summary.py -v`

Expected: PASS

- [ ] **Step 5: Commit**（仅执行本计划或用户要求时）

```
feat: lock user-facing copy and progress summary
```

---

### Task 2: MemoryDoc + 目录路径 + status

**Files:**
- Create: `src/rsi_boot/memory/__init__.py`
- Create: `src/rsi_boot/memory/types.py`
- Create: `src/rsi_boot/memory/paths.py`
- Test: `tests/test_memory_types.py`

**Interfaces:**
- Produces:

```python
MEMORY_TYPES = (
    "prohibition", "convention", "documentation", "skill",
    "episode", "gene_case", "teaching_case", "pattern",
)
LEGACY_TYPE_MAP = {
    "experience": "convention",
    "architecture": "documentation",
    "faq": "documentation",
}

class MemoryDoc(BaseModel):
    id: str  # 32 hex
    type: str
    title: str
    content: str
    status: str = "active"
    domain: str | None = None
    tags: list[str] = []
    roles: list[str] = []
    source: str | None = None
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    refs: list[str] = []
    extra: dict = {}
    payload: dict = {}
    path: str | None = None  # 相对 .rsi 的 posix 路径，写入后回填

def type_from_legacy(content_type: str) -> tuple[str, dict]:
    """返回 (type, extra_update)。未列出的 type 原样；architecture/faq/experience 按表。"""

def status_from_path(rel_posix: str) -> str:
    """memory/pending/** → pending_review；memory/archive/** → archived；
    teaching-cases/pending/ → active；其余 memory/ → active。"""

def official_dir(rsi_dir: Path, type: str) -> Path: ...
def pending_dir(rsi_dir: Path, type: str) -> Path: ...
# pending 仅 prohibition/convention/skill
```

- [ ] **Step 1: Write the failing test**

```python
from rsi_boot.memory.types import status_from_path, type_from_legacy

def test_teaching_pending_is_active():
    assert status_from_path("memory/teaching-cases/pending/x.yaml") == "active"

def test_norm_pending_is_review():
    assert status_from_path("memory/pending/prohibitions/x.yaml") == "pending_review"

def test_legacy_experience():
    typ, extra = type_from_legacy("experience")
    assert typ == "convention"
    assert extra["legacy_type"] == "experience"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_memory_types.py -v`

Expected: FAIL

- [ ] **Step 3: Implement types.py / paths.py**

未知顶层字段：Pydantic `model_config = extra="forbid"`。`id` 用 `Field(pattern=r"^[0-9a-f]{32}$")`。`title` max 120，`content` max 20000。skill 允许用 `body` 别名：`validation_alias` 或 `model_validator` 把 `body` 收成 `content`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -X utf8 -m pytest tests/test_memory_types.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```
feat: add MemoryDoc schema and directory status rules
```

---

### Task 3: YAML store（原子写、同 id 教学对）

**Files:**
- Create: `src/rsi_boot/memory/store.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `MemoryDoc`、`paths.py`
- Produces:

```python
class MemoryStore:
    def __init__(self, rsi_dir: Path): ...
    def write(self, doc: MemoryDoc, *, dest: Path) -> MemoryDoc:
        """写 dest.tmp → os.replace；回写 status 与 path；返回 doc。"""
    def read(self, id: str) -> MemoryDoc:
        """同 id 教学对：优先 teaching-cases。找不到 raise FileNotFoundError。"""
    def move(self, id: str, dest_dir: Path) -> MemoryDoc: ...
    def list_official(self, type: str | None = None) -> list[MemoryDoc]: ...
    def list_pending(self, type: str | None = None) -> list[MemoryDoc]: ...
```

写盘用 `yaml.safe_dump`，`allow_unicode=True`，`sort_keys=False`。读用 `safe_load`。未知顶层字段拒绝（`t("YAML_INVALID", lang, path=…)`），并 append `logs/errors.jsonl`。`write` 前对 `title`/`content`/`payload` 走现网 `masking.mask_text`（redact-not-drop）。进程内一把 `asyncio.Lock` 覆盖写 YAML / catalog / 刷新 cache。文件名 `{slug}--{id8}.yaml`。

- [ ] **Step 1: Write the failing test**

```python
def test_write_then_read(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段")
    dest = official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml"
    store.write(doc, dest=dest)
    got = store.read("a"*32)
    assert got.title == "禁止 SELECT *"
    assert got.status == "active"

def test_same_id_teaching_pair_prefers_case(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    tid = "c" * 32
    case = MemoryDoc(id=tid, type="teaching_case", title="t", content="case body")
    gene = MemoryDoc(id=tid, type="gene_case", title="t", content="index")
    store.write(case, dest=official_dir(store.rsi_dir, "teaching_case") / "t--cccccccc.yaml")
    store.write(gene, dest=official_dir(store.rsi_dir, "gene_case") / "manual" / "t--cccccccc.yaml")
    assert store.read(tid).type == "teaching_case"
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: atomic YAML memory store
```

---

### Task 4: JSONL 事件日志（retrieved 为 id）

**Files:**
- Create: `src/rsi_boot/memory/logstore.py`
- Test: `tests/test_logstore.py`

**Interfaces:**

```python
def append_event(rsi_dir: Path, event: dict) -> None:
    """校验 retrieved 若存在则每项为 32 hex；写 logs/events.jsonl。
    当前文件超 50MB 则滚到 events-YYYYMM.jsonl 再写新行。"""

def iter_events(rsi_dir: Path, *, kinds: set[str] | None = None):
    """扫 events.jsonl 与最近一个 events-YYYYMM.jsonl；坏行跳过并记 errors.jsonl。"""
```

新事件不得写 `retrieved` 为 domain/tag。migrate 回填可用 `retrieved_legacy_tags`。

- [ ] **Step 1: Write the failing test**

```python
def test_append_rejects_non_id_retrieved(tmp_path):
    with pytest.raises(ValueError, match="retrieved"):
        append_event(tmp_path, {"id": "e1", "kind": "recall", "retrieved": ["database"]})

def test_append_accepts_ids(tmp_path):
    append_event(tmp_path, {"id": "e1", "kind": "recall", "retrieved": ["a"*32], "task": "x"})
    rows = list(iter_events(tmp_path))
    assert rows[0]["retrieved"] == ["a"*32]
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: append-only event log with document-id retrieved
```

---

### Task 5: P0 意图扩展 + 整篇倒排（禁止精确标题命中）

**Files:**
- Create: `src/rsi_boot/rag/__init__.py`
- Create: `src/rsi_boot/rag/query.py`
- Create: `src/rsi_boot/rag/index.py`
- Create: `src/rsi_boot/rag/retriever.py`
- Test: `tests/test_rag_index.py`、`tests/test_rag_intent.py`

**Interfaces:**
- 复用 `preprocessor.intent_classifier.detect_intent`（规则，零 Key）。
- 从现网 `knowledge/retriever.py` 抽出 CJK bigram 到 `rag/index.py`。

```python
INTENT_SYNONYMS = {
    "星号": ["SELECT", "*", "列名"],
    "所有列": ["SELECT", "*", "列名"],
    "star": ["SELECT", "*", "列名"],
    "*": ["SELECT", "列名"],
    "禁止项": ["prohibition"],
    "must not": ["prohibition"],
}

def expand_query(task: str, *, role: str | None = None) -> tuple[str, str, float]:
    """返回 (expanded_text, intent, confidence)。
    expanded = 原文 + 意图名 + INTENT_SYNONYMS + preserve_spans 抽出的 SQL。
    不得把 expanded 设成某条 title。terms.yaml 只追加，不删内置。"""

def tokenize(text: str) -> list[str]: ...
def build_index(docs: list[MemoryDoc]) -> dict: ...
def search(index: dict, query: str, *, types: set[str] | None, top_n: int) -> list[tuple[str, float]]:
    """只按词袋/IDF 打分。实现中不得出现 `doc.title == query`。"""
```

P0 **不切片**。禁止项置顶仍是「正式目录全量」，与检索分无关。`items` 必须走 `expand_query` + `search`。

- [ ] **Step 1: Write the failing tests**

```python
def test_paraphrase_hits_without_exact_title():
    docs = [MemoryDoc(
        id="b"*32, type="convention",
        title="禁止 SELECT *", content="查询必须显式列出列名，禁止星号",
    )]
    idx = build_index(docs)
    expanded, intent, _ = expand_query("别把所有列一次查出来")
    assert expanded != "禁止 SELECT *"
    hits = search(idx, expanded, top_n=5)
    assert hits and hits[0][0] == "b"*32

def test_english_paraphrase_hits_chinese_doc():
    docs = [MemoryDoc(id="c"*32, type="convention",
                      title="禁止 SELECT *", content="必须写列名")]
    idx = build_index(docs)
    expanded, _, _ = expand_query("don't select star from the table")
    hits = search(idx, expanded, top_n=5)
    assert any(doc_id == "c"*32 for doc_id, _ in hits)

def test_retriever_source_has_no_exact_title_shortcut():
    from pathlib import Path
    src = Path("src/rsi_boot/rag/retriever.py").read_text(encoding="utf-8")
    assert "title ==" not in src and "title==" not in src
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: intent-expanded inverted retrieval without exact title match
```

---

### Task 6: 注入器改读 YAML（无约定 mdc）

**Files:**
- Modify: `src/rsi_boot/injector/rule_injector.py`
- Modify: `src/rsi_boot/injector/targets.py`（决策块仍 ≤800 字；去掉「清库先 rsi wipe」里过时的「rsi.db」表述，改为「文件记忆」）
- Test: `tests/test_inject_yaml.py`

**Interfaces:**
- `RuleInjector.__init__(self, db=None, project_root=None, store: MemoryStore | None = None)`  
  `store` 优先；无 store 时保持现网 db 路径，避免本任务弄红 `tests/test_injector.py`。
- `_load_bundle` 只读正式 `prohibitions/` + `skills/*/skill.yaml` 的 name/description
- `rewrite` 后不得存在 `rsi-convention-*.mdc`
- 禁止项拼接超 32KB：有采纳数据按最近采纳裁；否则按 `updated_at` DESC，不得无序丢掉最新
- 启动由 Task 11b 调用一次 `rewrite`

决策块保留现网「有 decisions 时…不要让用户自己去终端跑 rsi」语义，库字样改成「记忆文件」。

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_inject_prohibition_not_convention(tmp_path):
    from rsi_boot.injector.rule_injector import RuleInjector
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml",
    )
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="约定用命名参数", content="不用位置参数"),
        dest=official_dir(store.rsi_dir, "convention") / "named--bbbbbbbb.yaml",
    )
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite()
    rules = tmp_path / ".cursor" / "rules"
    assert list(rules.glob("rsi-prohibition-*.mdc"))
    assert "禁止 SELECT *" in next(rules.glob("rsi-prohibition-*.mdc")).read_text(encoding="utf-8")
    assert list(rules.glob("rsi-convention-*.mdc")) == []
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: inject prohibitions from YAML only
```

---

### Task 7: 召回改读文件 + retrieved 为 id

**Files:**
- Modify: `src/rsi_boot/services/recall_service.py`
- Modify: `src/rsi_boot/feedback/implicit_tracker.py`（读 `retrieved` id）
- Test: `tests/test_recall_files.py`

**Interfaces:**
- `RecallService.__init__(..., store: MemoryStore | None = None, feedback_secret: str = "")`：`store` 优先；有 store 时 `retriever`/`arms`/`db` 可省略（内部用 `rag.search` + 默认臂 `recall-balanced`）。无 store 保持现网必填 `db`。
- `RecallService.recall(...)` 仍返回 dict。P0 键：`prohibitions` / `items` / `skills` / `feedback_token` / `recall_arm`。不要返回 `gene_cases` / `teaching_cases` / `episodes`。
- `items` 必须 `expand_query(task)` + 倒排，禁止 `title == task` 过滤。
- 写 `events.jsonl` 时 `retrieved` = 命中 id 列表；可记 `intent`（探测结果，不是知识标题）。
- `items` 每条含 `id`、`title`、`content`、`content_type`、`source_path`。P0 `heading` 可空；`weight` / `conflict_open` 有则填。
- 召回事件可带 `excerpt`（命中标题拼接），供后续 `modified` diff。

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_recall_logs_document_ids(tmp_path):
    from rsi_boot.memory.logstore import iter_events
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.recall_service import RecallService

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须写列名"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml",
    )
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="查询列清单", content="别把所有列一次查出来"),
        dest=official_dir(store.rsi_dir, "convention") / "cols--bbbbbbbb.yaml",
    )
    svc = RecallService(store=store, feedback_secret="test")
    payload = await svc.recall(task="列名", project_id="local")
    assert set(payload) >= {"prohibitions", "items", "skills", "feedback_token", "recall_arm"}
    assert "gene_cases" not in payload
    row = list(iter_events(tmp_path / ".rsi"))[-1]
    assert row["retrieved"] and all(len(x) == 32 and int(x, 16) >= 0 for x in row["retrieved"])
    assert isinstance(payload["skills"], list)
```

- [ ] **Step 2–4:** 红 → 实现 → 绿。`LogService.apply_feedback` 改为 append JSONL（可本任务先做最小 `kind=feedback` 行，Worker 改文件放到 Task 9）。

- [ ] **Step 5: Commit**

```
feat: recall from YAML and log retrieved document ids
```

---

### Task 8: 知识 add / delete / review 搬文件

**Files:**
- Modify: `src/rsi_boot/services/knowledge_service.py`
- Modify: `src/rsi_boot/api/tools/knowledge_review_tool.py`（`TOOL_DESCRIPTION = TOOL_DESC["review"]`）
- Modify: `src/rsi_boot/api/tools/knowledge_search_tool.py`（检索走 `expand_query`，禁止精确标题）
- Modify: `src/rsi_boot/api/tools/knowledge_add_tool.py`
- Test: `tests/test_knowledge_files.py`

**Interfaces:**
- `KnowledgeService.__init__(..., store: MemoryStore | None = None, project_root: Path | None = None)`：`store` 优先。
- add：prohibition/convention/skill → `pending/`；documentation / 案例 → 正式目录。返回 `{"id","path","message"}`。
- delete：`move` 到 `archive/`。
- review approve：pending → 正式；skill → `skills/<name>/skill.yaml`（name = `payload.name` 或 slug(title)）。然后 `injector.rewrite`。
- review reject：→ `archive/`，`extra.review=rejected`。
- 成功 `message` 用 `REVIEW_APPROVED` / `REVIEW_REJECTED`。

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_review_approve_moves_and_injects(tmp_path):
    from rsi_boot.memory.paths import official_dir, pending_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.knowledge_service import KnowledgeService

    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段")
    store.write(doc, dest=pending_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml")
    svc = KnowledgeService(store=store, project_root=tmp_path)
    out = await svc.review_approve("a"*32)
    assert store.read("a"*32).status == "active"
    assert official_dir(store.rsi_dir, "prohibition") in Path(out["moved"][0]["to"]).parents or "prohibitions" in out["moved"][0]["to"]
    assert list((tmp_path / ".cursor" / "rules").glob("rsi-prohibition-*.mdc"))

@pytest.mark.asyncio
async def test_review_reject_archives(tmp_path):
    from rsi_boot.memory.paths import pending_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc
    from rsi_boot.services.knowledge_service import KnowledgeService

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="x", content="y"),
        dest=pending_dir(store.rsi_dir, "convention") / "x--bbbbbbbb.yaml",
    )
    svc = KnowledgeService(store=store, project_root=tmp_path)
    await svc.review_reject("b"*32)
    got = store.read("b"*32)
    assert got.status == "archived"
    assert got.extra.get("review") == "rejected"
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: knowledge add and review as file moves
```

---

### Task 9: 反馈提取只写 pending 规范

**Files:**
- Create: `src/rsi_boot/learning/pipeline.py`（从 `KnowledgeExtractor` 规则式部分迁出写文件）
- Modify: `src/rsi_boot/learning/knowledge_extractor.py`（委托 pipeline；禁止 INSERT SQL）
- Test: `tests/test_extract_pending_yaml.py`

**Interfaces:**
- 过门槛的 rejected/modified → `pending/prohibitions/` 或 `pending/conventions/` + `logs/candidates.jsonl`
- 断言写出路径前缀必须是 `memory/pending/prohibitions` 或 `memory/pending/conventions`
- 不得写 `gene-map/`、`teaching-cases/`、`patterns/`

- [ ] **Step 1: Write the failing test**

```python
def test_feedback_extract_only_pending_norms(tmp_path):
    from rsi_boot.learning.pipeline import extract_from_feedback
    paths = extract_from_feedback(
        tmp_path / ".rsi",
        action="rejected",
        comment="不要再用 SELECT *",
        retrieved=["a"*32],
    )
    assert paths
    for p in paths:
        rel = p.as_posix()
        assert rel.startswith("memory/pending/prohibitions") or rel.startswith("memory/pending/conventions")
        assert "gene-map" not in rel and "teaching-cases" not in rel and "patterns" not in rel
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: write feedback extracts to pending YAML only
```

---

### Task 10: `rsi memory migrate`（进度 + 人话）

**Files:**
- Create: `src/rsi_boot/services/memory_migrate.py`
- Create: `src/rsi_boot/cli/memory_command.py`
- Modify: `src/rsi_boot/__main__.py`（`memory` 子命令；保留旧 `migrate adopt` 至本任务结束再标「请改用 rsi memory migrate」）
- Test: `tests/test_memory_migrate.py`、`tests/test_cli_memory.py`

**Interfaces:**

```python
async def migrate_workspace(
    project_root: Path,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> dict:
    """只读 sqlite；返回计数。非 dry_run 才 replace 文件并改名 .bak。"""
```

进度 8 段名称必须等于 `t("MIGRATE_PHASE_*", lang)`。无库：stderr `t("MIGRATE_NO_DB", lang)`，exit 3。中途失败：不改名 `rsi.db`，stderr `t("MIGRATE_HALF", lang)`，exit 3。成功再打 `t("MIGRATE_SUMMARY", lang, …)`。`migrate_workspace` 增加 `lang: str | None = None`。

`--json`：进度在 stderr，stdout 为计数 JSON。

映射必须一次卸完（规格 §4.8，禁止半切）：

| 源 | 目标 |
|----|------|
| `knowledge_items` | YAML，`type_from_legacy`；pending_review→`pending/`，active→正式，其余→`archive/` |
| `interaction_logs` | `events.jsonl`；能映射的 tag→`retrieved`，否则 `retrieved_legacy_tags` |
| `strategy_configs` 全部行 | `state/arms.yaml`（本波召回只读 intent=recall） |
| `rule_conflicts` + `host_judge_queue.json` | `state/conflicts.yaml` |
| `harness_proposals` / `config_snapshots` | `state/proposals/`、`state/snapshots/` |
| `user_profiles` | `state/profile.yaml` |
| `rule_artifacts` | `state/artifacts.yaml` |
| `extraction_candidates` | `logs/candidates.jsonl` |
| `project_configs` | `manifest.json` / `identity.json` |
| 提案槽 SKILL.md | `memory/skills/<name>/skill.yaml`（本波不做导出命令） |
| 计数与源路径 | `memory_migrate.yaml` |
| `rsi.db*` | `rsi.db.bak-*` |

migrate 本波仍可用现网 `SQLiteClient`（aiosqlite）。P0 不强制改成标准库 `sqlite3`。

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_migrate_no_db_exit_3(tmp_path, capsys, monkeypatch):
    from rsi_boot.cli.memory_command import run_memory
    from rsi_boot.ux.messages import t
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSI_LANG", "zh")
    code = run_memory(["migrate"])
    assert code == 3
    assert capsys.readouterr().err.strip() == t("MIGRATE_NO_DB", "zh")

@pytest.mark.asyncio
async def test_migrate_writes_yaml_and_renames_db(tmp_path):
    from rsi_boot.data.migrate import migrate
    from rsi_boot.data.sqlite import SQLiteClient
    from rsi_boot.services.memory_migrate import migrate_workspace

    rsi = tmp_path / ".rsi"
    rsi.mkdir()
    db = SQLiteClient(rsi / "rsi.db")
    await migrate(db)
    now = "2026-09-13T00:00:00+00:00"
    await db.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("a"*32, "local", "禁止 SELECT *", "必须列字段", "prohibition", "active", now, now),
    )
    await db.commit()
    await db.close()
    await migrate_workspace(tmp_path, lang="zh")
    assert not (rsi / "rsi.db").exists()
    assert list((rsi / "rsi.db.bak-*").parent.glob("rsi.db.bak-*")) or list(rsi.glob("rsi.db.bak-*"))
    assert list((rsi / "memory" / "prohibitions").glob("*.yaml"))
```

- [ ] **Step 2–4:** 红 → 实现 → 绿。本任务**允许**现网服务继续 import sqlite。禁止在本任务把 `SQLiteClient` 从 `bootstrap.py` 删掉。

- [ ] **Step 5: Commit**

```
feat: migrate sqlite memory to YAML with progress
```

---

### Task 11a: 其余读库点的文件后端（仍不切 Runtime）

本任务只给冲突/统计/提案/臂/日志写文件适配，**不改** `build_runtime`。现网 Runtime 继续连 sqlite。

**Files:**
- Modify: `src/rsi_boot/injector/conflict.py`（扫 yaml + `retrieved` id；写 `state/conflicts.yaml`）
- Modify: `src/rsi_boot/services/stats_service.py`（读 yaml + `events.jsonl`）
- Modify: `src/rsi_boot/learning/proposal_engine.py`、`src/rsi_boot/learning/snapshot_store.py`（`state/proposals/`、`state/snapshots/`）
- Modify: `src/rsi_boot/strategy/recall.py`（`state/arms.yaml`）
- Modify: `src/rsi_boot/services/log_service.py`、`src/rsi_boot/feedback/implicit_tracker.py`（`retrieved` 为文档 id；migrate 行可读 `retrieved_legacy_tags`）
- Modify: `src/rsi_boot/api/tools/conflicts_tool.py`、`stats_tool.py`、`review_tool.py`（可选 `store=`）
- Test: `tests/test_conflicts_files.py`、`tests/test_stats_files.py`、`tests/test_review_files.py`

**Interfaces:**
- 各服务 ctor 加 `store: MemoryStore | None = None`；有 store 走文件，无 store 保持旧 db。
- `conflict.py` 禁止再 `retrieved_tags LIKE`。
- 语义与现网 list/resolve/stats/snapshot 一致，只换载体。

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_conflict_scan_uses_document_ids(tmp_path):
    from rsi_boot.injector.conflict import ConflictDetector
    from rsi_boot.memory.logstore import append_event
    from rsi_boot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / ".rsi")
    append_event(tmp_path / ".rsi", {
        "id": "e1", "kind": "recall", "retrieved": ["a"*32], "task": "列名",
    })
    det = ConflictDetector(store=store, project_root=tmp_path)
    found = await det.scan()
    assert all("retrieved_tags" not in str(c) for c in found)

def test_conflict_source_has_no_like_tags():
    src = Path("src/rsi_boot/injector/conflict.py").read_text(encoding="utf-8")
    assert "retrieved_tags" not in src
```

- [ ] **Step 2–4:** 红 → 实现 → 绿。本任务结束只跑本任务新测，不要全量改 conftest。

- [ ] **Step 5: Commit**

```
feat: file backends for conflicts stats review and arms
```

---

### Task 11b: Runtime 切断 sqlite + 启动注入 + YAML watch

**Files:**
- Modify: `src/rsi_boot/bootstrap.py`（`build_runtime` 不再构造/`connect` `SQLiteClient`；持有 `MemoryStore` / index；所有服务传入 `store=`）
- Modify: `src/rsi_boot/__main__.py` `run_serve`：启动 `await injector.rewrite`；`--watch` 只启 `memory/watch.py`；**不要**构造 `IncrementalLearner`
- Modify: `src/rsi_boot/scheduler/manager.py`（extractor 走文件 pipeline；删除对 `db` 的依赖）
- Create: `src/rsi_boot/memory/watch.py`
- Modify: 11a 列出的服务，**删除 db 分支**
- Modify: `tests/conftest.py`（默认 fixture 改为 `MemoryStore`；sqlite fixture 仅给 `tests/test_memory_migrate.py` / `tests/test_sqlite_retry.py`）
- Modify: 现网仍构造 `SQLiteClient` 的测试（至少：`test_injector.py`、`test_recall.py`、`test_recall_decisions.py`、`test_bootstrap*.py`、`test_harness.py`、`test_conflict*.py`、`test_review_queue.py`、`test_knowledge_freshness.py`、`test_project_isolation.py`）
- Test: `tests/test_runtime_no_sqlite.py`、`tests/test_watch_yaml.py`

**Interfaces:**
- spy：`SQLiteClient.connect` **与** `SQLiteClient.__init__` 在 `build_runtime` / `run_serve` 期间不得被调用。
- `Runtime` 无 `.db` 属性；`close` 只 flush 日志。
- 无正式禁止项时 rewrite 仍运行，清掉陈旧 `rsi-convention-*.mdc`。
- watch 防抖 ≥300ms，只匹配 `memory/**/*.yaml`。
- 此后除 `memory_migrate.py` 与 sqlite 单测外，`src/rsi_boot/**` 不得 import `SQLiteClient`。

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_build_runtime_does_not_construct_sqlite(monkeypatch, tmp_path):
    hits = {"n": 0}
    def boom(*_a, **_k):
        hits["n"] += 1
        raise AssertionError("sqlite")
    monkeypatch.setattr("rsi_boot.data.sqlite.SQLiteClient.__init__", boom)
    monkeypatch.setattr("rsi_boot.data.sqlite.SQLiteClient.connect", boom)
    from rsi_boot.bootstrap import build_runtime
    rt = build_runtime(project_root=tmp_path)
    assert hits["n"] == 0
    assert not hasattr(rt, "db")
    await rt.close()

@pytest.mark.asyncio
async def test_watch_only_yaml(tmp_path):
    from rsi_boot.memory.watch import YamlWatcher
    seen: list[str] = []
    w = YamlWatcher(tmp_path / ".rsi" / "memory", on_change=lambda p: seen.append(p.name))
    (tmp_path / ".rsi" / "memory" / "prohibitions").mkdir(parents=True)
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    await w.poll_once()
    assert "README.md" not in seen
```

- [ ] **Step 2–4:** 红 → 实现 → 绿。本任务结束必须：

```
python -X utf8 -m pytest tests -q
```

- [ ] **Step 5: Commit**

```
feat: file-backed runtime with startup inject and yaml watch
```

---

### Task 12: MCP `rsi_learn` + `rsi_memory`（P0 动作 + 文案）

**Files:**
- Create: `src/rsi_boot/learning/teaching.py`
- Create: `src/rsi_boot/api/tools/learn_tool.py`
- Create: `src/rsi_boot/api/tools/memory_tool.py`
- Modify: `src/rsi_boot/api/mcp_server.py`（11 工具；`_INSTRUCTIONS = TOOL_DESC["recall"]`）
- Modify: `src/rsi_boot/api/tools/recall_tool.py`（`TOOL_DESCRIPTION = TOOL_DESC["recall"]`）
- Test: `tests/test_learn_tool.py`、`tests/test_memory_tool.py`

**Interfaces:**
- P0 learn actions：`teach_catch` / `teach_record` / `skip` / `rubric`
- 其余 action 返回 `{"status":"error","code":"not_in_phase","message": t("PHASE_*", lang)}`
  - promote → `t("PHASE_PROMOTE", lang)`
  - audit_* / extract → `t("PHASE_AUDIT", lang)`
- `teach_catch` 写 `state/teach-drafts/{id}.yaml`，`message=t("TEACH_CATCH_SAVED", lang, id=…)`
- `teach_record` 要求 `lesson.correct_fix` 非空；写 teaching-case + 同 id manual；`promote_to_pattern=true` 另写 `patterns/`；`message=t("TEACH_RECORDED", lang, path=…)`；`data.closeout` 为清单
- `rsi_memory`：`index` / `open` / `reindex`；`graph` → `PHASE_GRAPH`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_promote_not_in_phase():
    out = await learn_tool.handle(runtime, {"action": "promote"})
    assert out["code"] == "not_in_phase"
    assert out["message"] == t("PHASE_PROMOTE", "zh")

@pytest.mark.asyncio
async def test_teach_record_writes_pair(tmp_path):
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t

    rt = build_runtime(project_root=tmp_path)
    out = await handle(rt, {
        "action": "teach_record",
        "lesson": {
            "wrong_action": "SELECT *",
            "correct_fix": "列出列名",
            "error_signature": "select-star",
            "failure_type": "sql",
        },
    })
    assert out["status"] == "success"
    assert out["message"] == t("TEACH_RECORDED", "zh", path=out["data"]["path"])
    assert "teaching-cases" in out["data"]["path"]
    assert out["data"]["closeout"]
    assert list((tmp_path / ".rsi" / "memory" / "gene-map" / "manual").glob("*.yaml"))
```

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: add rsi_learn and rsi_memory MCP tools
```

---

### Task 13: CLI `rsi memory` / `rsi learn`

**Files:**
- Modify: `src/rsi_boot/cli/memory_command.py`（补 index/open/reindex）
- Create: `src/rsi_boot/cli/learn_command.py`
- Modify: `src/rsi_boot/__main__.py`（`--lang`；help 用 `t(..., locale_lang())`：memory=迁出/索引/打开 或 Migrate/index/open；learn=教学与收工 或 Teaching and closeout）
- Test: `tests/test_cli_memory.py`、`tests/test_cli_learn.py`

**Interfaces:**
- `rsi memory` 无子命令 → exit 2，stderr `t("MEMORY_NEED_SUB", lang)`
- `rsi learn` 无 action → exit 2，stderr `t("LEARN_NEED_ACTION", lang)`
- `rsi memory index` 默认表格：`id  type  status  title  path`；`--json` 打数组
- `rsi memory open <id>` 打印 YAML 正文；找不到 exit 3，stderr `t("NOT_FOUND", lang, id=…)`
- `rsi memory reindex`：重建 `cache/inverted.json`；删 cache 后再跑，Top5 重合 ≥ 80%
- `rsi learn teach_record --file lesson.yaml` 与 MCP 同语义
- argparse `help` 不要写「数据库」「rsi.db」

- [ ] **Step 1: Write the failing tests**（exit 2 固定句）

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: CLI for file memory and learn
```

---

### Task 14: bootstrap / wipe / README 文案与写 YAML

**Files:**
- Modify: `src/rsi_boot/cli/bootstrap_command.py`（写入改 `MemoryStore`；`progress.phase(t("BOOTSTRAP_PHASE_INIT", lang))`，禁止手写「初始化数据库」）
- Modify: `src/rsi_boot/cli/wipe_command.py`（删除 `memory/` `logs/` `cache/` `audit/` `state/` 中除 identity 外约定文件；`_YES_HINT = lambda lang: t("WIPE_HINT", lang)`）
- Modify: `src/rsi_boot/injector/targets.py` `_DECISIONS_BODY`：学完走 `rsi_conflicts`，不要再写 `host_judge_queue.json` 为唯一入口
- Modify: `README.md`（先 `rsi memory migrate` 再 `rsi serve`；约定不再进规则文件）
- Test: 更新 `tests/test_wipe.py` / `tests/test_bootstrap.py` 里「rsi.db」断言

**Interfaces:**
- bootstrap 规范类默认 pending（与 MCP add 一致），原文 allowlist 才直通。
- wipe `--yes` 成功句：`t("WIPE_DONE", lang)` + 路径列表；保留句：`t("WIPE_KEPT", lang, path="identity.json")`

- [ ] **Step 1: Write/update failing tests**

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: bootstrap and wipe file-memory copy
```

**P0 验收（本任务后必须全绿）：**

```
python -X utf8 -m pytest tests/test_ux_lang.py tests/test_ux_messages.py tests/test_progress_summary.py tests/test_memory_types.py tests/test_memory_store.py tests/test_logstore.py tests/test_rag_index.py tests/test_rag_intent.py tests/test_inject_yaml.py tests/test_recall_files.py tests/test_knowledge_files.py tests/test_extract_pending_yaml.py tests/test_memory_migrate.py tests/test_cli_memory.py tests/test_conflicts_files.py tests/test_stats_files.py tests/test_review_files.py tests/test_runtime_no_sqlite.py tests/test_watch_yaml.py tests/test_learn_tool.py tests/test_memory_tool.py tests/test_cli_learn.py -v
```

手工：`rsi memory migrate` 能看到 8 段进度且最后一行是「迁出完成」不是「学习完成」；`rsi serve` 后无 `rsi-convention-*.mdc`；手写 prohibition 进正式目录后启动即出现 mdc。

---

### Task 15: P1 — 切片召回 + 教学/基因返回 + 审计/收工

**Files:**
- Create: `src/rsi_boot/rag/chunker.py`
- Create: `src/rsi_boot/learning/gene_map.py`
- Create: `src/rsi_boot/learning/audit_store.py`
- Modify: `src/rsi_boot/services/recall_service.py`（P1 返回 `gene_cases` / `teaching_cases` / `episodes`；teaching 与 manual 去重）
- Modify: `src/rsi_boot/api/tools/learn_tool.py`（实现 `audit_start` / `audit_probes` / `audit_report` / `audit_finish` / `extract`）
- Modify: `src/rsi_boot/ux/messages.py`（`EXTRACT_CLOSEOUT`、审计短句）
- Test: `tests/test_chunker.py`、`tests/test_recall_gene.py`、`tests/test_audit_learn.py`

**Interfaces:**
- 切片：空行分段；行首 `## ` 为可选小节；超 800 字再切。gene/teaching/情节同一套 `expand_query`，不靠 `error_signature == task`。
- extract **不得**写 `pending/prohibitions` 或 `pending/conventions`。
- `audit_report` 最小字段：`scope` / `overall` / `findings` / `probe_summary` / `disclaimer_partial`。
- `audit_finish` 写 `state/audit-session.yaml` 的 `closed_at`。
- extract 成功 `message=t("EXTRACT_CLOSEOUT", lang)`，`data.closeout` 供宿主照抄。
- 情节：扫 `events.jsonl` 情境指纹，不要求先有 episode yaml。物化 `episodes/*.yaml` 仅当同一 task 指纹反馈 ≥ 2，或 rating ≥ 4，或 rejected+评语。
- 召回可带 `hint=t("HINT_TEACH", lang)`。
- 删掉 `cache/` 后 `reindex`，无向量 Top5 重合 ≥ 80%（相对重建前）。

- [ ] **Step 1: Write the failing tests**（去重、extract 路径互斥、session+pass 必须 `disclaimer_partial`）

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: chunked recall and closeout learn/audit
```

---

### Task 16: P2 — 晋升、收工注入块、graph

**Files:**
- Create: `src/rsi_boot/learning/promote.py`
- Modify: `src/rsi_boot/injector/targets.py`（AGENTS 托管块追加 teach/audit/closeout，总长 ≤800）
- Create: `src/rsi_boot/memory/graph.py`
- Modify: learn/memory 工具去掉这些 action 的 `not_in_phase`
- Test: `tests/test_promote.py`、`tests/test_closeout_inject.py`、`tests/test_memory_graph.py`

**Interfaces:**
- promote：精确 `error_signature+failure_type` 成功 ≥3 或 teaching 勾选 → 只写 `pending/prohibitions|conventions|skills`
- 不写 `patterns/`，不调用 inject
- `rsi_memory graph` 返回 mermaid **文本**（不落 md 知识文件）
- 注入块必须出现：`teach_catch` / `teach_record` / `audit_finish` / 「最终总结照抄 closeout 路径」
- 可选：`rsi memory import` 走原文直通闸门（allowlist）；MCP add 规范类仍 pending

- [ ] **Step 1: Write the failing tests**

- [ ] **Step 2–4:** 红 → 实现 → 绿

- [ ] **Step 5: Commit**

```
feat: promote pending norms, closeout inject, memory graph
```

---

## Spec coverage（自检）

| 规格项 | 任务 |
|--------|------|
| YAML SoT / 原子写 / schema | 2, 3 |
| events.jsonl / retrieved id | 4, 7 |
| 类型映射 / teaching-cases/pending 不是待审 | 2 |
| 整篇倒排 P0 / 切片 P1 | 5, 15 |
| 意图 + RAG，禁止精确标题召回 | 5, 7, 15 |
| 注入只禁止项 + 启动 rewrite | 6, 11b |
| 反馈提取 vs extract 目录独占 | 9, 15 |
| review 搬文件 / reject archive | 8 |
| migrate 一次卸完 + 进度文案 | 10, 1 |
| serve 不 connect / watch 只 yaml | 11b |
| rsi_learn P0/P1/P2 分期与 not_in_phase 文案 | 12, 15, 16 |
| rsi_memory index/open/reindex/graph | 12, 16 |
| teach 同 id manual / promote_to_pattern → patterns/ | 12 |
| closeout 清单 + 注入纪律块 | 15, 16 |
| bootstrap/wipe 文件化与文案 | 14 |
| 交互：进度、exit、MCP 短句、zh/en 匹配 | 1, 10, 12, 13 |
| 写盘脱敏 | 3 |
| knowledge_search 意图检索 | 8 |
| rsi_conflicts / rsi_stats / rsi_review 文件后端 | 11a |
| 存量测试改文件后端、serve 零 sqlite | 11b |

P0（Task 1–14，含 11a/11b）可交付迁出、召回、注入、教学落盘。Task 11b 是换芯日：当天全量 `pytest tests` 必须绿。
