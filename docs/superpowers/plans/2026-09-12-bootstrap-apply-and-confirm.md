# Bootstrap 直通 / 整批确认 / 对话内抉择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仓库原文无冲突则直通生效；抽取与冲突进对话抉择（agent 调 MCP 落库）；切片按知识面/体量；日常 `auto-extract` 不被 bootstrap 整批误伤。

**Architecture:** 先改纯函数（切片、冲突门、分桶画像），再改写入车道与报告，最后接 CLI/MCP（`accept`、`recall.decisions`、`conflicts explain`）。冲突裁决动词与现网 version 组对齐：`keep_item` / `keep_peer` / `coexist`。

**Tech Stack:** Python 3.12、pytest、aiosqlite、现有 MCP 工具（不新增工具名）。Windows 上测试命令一律 `python -X utf8 -m pytest …`，PowerShell 不用 `&&`。

**Spec:** `docs/superpowers/specs/2026-09-12-bootstrap-apply-and-confirm-design.md`

## Global Constraints

- 零 Key：冲突检测只用启发式，不调 LLM。
- 不改 MCP 工具名；`rsi_conflicts` 只加 `explain` action。
- `rsi knowledge accept` 的 WHERE 必须含 `bootstrap_run_id`；`source_url='auto-extract'` 一律不碰。
- `review_queue_cap` 只扫描带 `bootstrap_run_id` 的行；超出默认 500 只警告，不把 B/C 改成 archived。
- watch 静默学语义不改（同 source 仍直写 `active`）。
- 第一期不做 archived 一键提升；已学项目清库重学。
- TDD：先红后绿。提交仅在用户要求或本计划执行时按步提交。
- 裁决映射：`keep_item` = 保留 `rule_conflicts.item_id` 一侧并归档对侧；`keep_peer` = 保留 `user_rule_path` 一侧；`coexist` = 两侧转/保持可用且不再提醒。

---

## File map

| File | Responsibility |
|------|----------------|
| `src/rsi_boot/scanner/document_scanner.py` | 自适应切 + 目录/摘要 chunk |
| `src/rsi_boot/scanner/config_scanner.py` | 框架/测试框架按语言分桶，不锁死先解析结果 |
| `src/rsi_boot/scanner/profile_generator.py` | 画像写分桶字符串 |
| `src/rsi_boot/scanner/conflict_gate.py` | **新建**：拟写入草稿 → hold 集合 + ConflictDraft |
| `src/rsi_boot/injector/conflict.py` | persist / resolve / explain `doc_code`+`incoherent` |
| `src/rsi_boot/cli/bootstrap_command.py` | run id、按车道写 status、cap 收窄 |
| `src/rsi_boot/scanner/report.py` | `.rsi/bootstrap_report.md` |
| `src/rsi_boot/services/knowledge_service.py` | `review_batch` 过滤 bootstrap / auto-extract |
| `src/rsi_boot/cli/knowledge_accept.py` | **新建**：CLI 兜底整批 |
| `src/rsi_boot/__main__.py` | `knowledge accept` 子命令 |
| `src/rsi_boot/learning/knowledge_extractor.py` | 落库后 incoherent vs active |
| `src/rsi_boot/services/decision_queue.py` | **新建**：sticky / suppress / 组卡 |
| `src/rsi_boot/services/recall_service.py` | payload 加 `decisions` |
| `src/rsi_boot/api/tools/recall_tool.py` | 工具描述驱动 agent 自己调工具 |
| `src/rsi_boot/api/tools/conflicts_tool.py` | `explain` |
| `src/rsi_boot/injector/targets.py` | 常驻 `rsi-decisions.mdc` |
| `src/rsi_boot/config/default.yaml` | `bootstrap.chunk.*`、cap 注释改为 500 警告 |
| `src/rsi_boot/scanner/incremental.py` | `slice_document` 新签名（stats 可忽略） |
| `README.md` | 直通 / 对话抉择 / 清库重学一句 |

---

### Task 1: 自适应文档切片

**Files:**
- Modify: `src/rsi_boot/scanner/document_scanner.py`
- Modify: `src/rsi_boot/scanner/incremental.py`（继续 `for chunk in slice_document(path)`，兼容新返回）
- Test: `tests/test_adaptive_slice.py`

**Interfaces:**
- Consumes: `estimate_tokens` in `validator.py`
- Produces:

```python
@dataclass
class DocChunk:
    source: Path
    title: str
    content: str
    kind: str = "section"  # section | index | digest

@dataclass
class SliceResult:
    chunks: list[DocChunk]
    merged_tiny: int = 0
    split_large: int = 0
    skipped_tiny: int = 0
    index_written: int = 0

def slice_document(
    path: Path,
    *,
    target_tokens: int = 400,
    min_tokens: int = 80,
    max_tokens: int = 1500,
) -> SliceResult: ...
```

- `slice_document` 必须仍可被 `incremental.py` 当可迭代使用：给 `SliceResult` 实现 `__iter__` 产出 `chunks`，这样旧 `for chunk in slice_document(path)` 不改也能跑。

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from rsi_boot.scanner.document_scanner import slice_document
from rsi_boot.scanner.validator import estimate_tokens

def test_tiny_headings_merge(tmp_path: Path):
    body = "\n".join(f"## H{i}\n\nshort.\n" for i in range(10))
    p = tmp_path / "a.md"
    p.write_text(body, encoding="utf-8")
    result = slice_document(p)
    assert len(result.chunks) < 10
    assert result.merged_tiny >= 1

def test_huge_body_splits_and_writes_index(tmp_path: Path):
    p = tmp_path / "big.md"
    p.write_text("x" * 6000, encoding="utf-8")  # 1500 tokens
    result = slice_document(p, max_tokens=400)
    assert result.chunks
    assert all(estimate_tokens(c.content) <= 400 for c in result.chunks if c.kind != "index")
    assert any(c.kind == "index" for c in result.chunks) or result.index_written >= 1

def test_license_is_single_digest(tmp_path: Path):
    p = tmp_path / "LICENSE"
    p.write_text("MIT " + ("copyright line\n" * 80), encoding="utf-8")
    result = slice_document(p)
    assert len(result.chunks) == 1
    assert result.chunks[0].kind == "digest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_adaptive_slice.py -q`

Expected: FAIL（`SliceResult` 不存在或旧函数返回 list）

- [ ] **Step 3: Write minimal implementation**

在 `document_scanner.py`：LICENSE/COPYING/changelog 文件名 → 单条 `digest`（截到 `max_tokens`）。有标题则切小节；`estimate_tokens < min_tokens` 的与下一节合并。无标题超 `max_tokens` 按段落切，子点 ≥ 3 时追加 `kind=index` 目录（标题列表，不含全文）。`SliceResult.__iter__` yield chunks。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_adaptive_slice.py tests/test_bootstrap.py -q`

Expected: PASS（旧 bootstrap 切片循环仍可用）

- [ ] **Step 5: Commit**

```text
feat: 文档按目标尺寸切片并合并碎块
```

---

### Task 2: 分桶画像（双栈）

**Files:**
- Modify: `src/rsi_boot/scanner/config_scanner.py`
- Modify: `src/rsi_boot/scanner/profile_generator.py`
- Test: `tests/test_dual_stack_profile.py`

**Interfaces:**
- Consumes: 现有 `_parse_pyproject` / `_parse_pom`
- Produces: `ConfigInsights.language` 可为 `"java,python"`；`framework` / `test_framework` 为 `"python:pydantic; java:"` 这种分桶串，或新增字段 `frameworks_by_lang: dict[str, list[str]]` 并由 `build_profile` 格式化。**采用新增字段**，避免破坏只读 `summary_text` 的旧断言：

```python
# ConfigInsights 增加
frameworks_by_lang: dict[str, list[str]] = field(default_factory=dict)
test_frameworks_by_lang: dict[str, list[str]] = field(default_factory=dict)

def format_stack_buckets(by_lang: dict[str, list[str]]) -> str:
    """python:pydantic+pytest; java:junit — 空桶省略"""
```

解析时：`_parse_pyproject` 写入 `frameworks_by_lang["python"]`，**不要**再 `insights.framework = insights.framework or label` 抢占全局。语言占比：所有 ≥40% 的语言都列入 `language`（逗号连接，字母序）。`build_profile` 的 `role_specific["framework"]` = `format_stack_buckets(frameworks_by_lang)`。

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from rsi_boot.scanner.config_scanner import scan_configs
from rsi_boot.scanner.profile_generator import build_profile

def test_java_majority_does_not_steal_pydantic_as_sole_framework(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pom.xml").write_text(
        "<project><artifactId>junit-jupiter</artifactId></project>", encoding="utf-8",
    )
    java = [tmp_path / f"A{i}.java" for i in range(6)]
    py = [tmp_path / "a.py"]
    for p in java + py:
        p.write_text("class A {}", encoding="utf-8")
    insights = scan_configs(
        tmp_path,
        [tmp_path / "pyproject.toml", tmp_path / "pom.xml"],
        [], [], java + py,
    )
    assert "java" in (insights.language or "")
    assert "python" in (insights.language or "")
    profile = build_profile("u", "local", insights, "moderate", "weak")
    fw = profile.preferences.role_specific.get("framework", "")
    assert "pydantic" in fw
    assert "java" in (insights.language or "")
    assert fw != "pydantic"  # 不得再单独冒充全局框架
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python -X utf8 -m pytest tests/test_dual_stack_profile.py -q`

- [ ] **Step 3: Implement**

改解析函数往分桶写；`scan_configs` 末尾用分桶填 `framework`/`test_framework` 展示串（`format_stack_buckets`）。语言：收集所有 `count/total >= 0.4` 的语言，排序 join。

- [ ] **Step 4: Run**

Run: `python -X utf8 -m pytest tests/test_dual_stack_profile.py tests/test_java_profile.py tests/test_python_config.py -q`

Expected: PASS（若旧测试断言 `framework == "pydantic"` 且 language python-only，保持通过）

- [ ] **Step 5: Commit**

```text
feat: 多清单并存时画像按语言分桶
```

---

### Task 3: conflict_gate 纯函数

**Files:**
- Create: `src/rsi_boot/scanner/conflict_gate.py`
- Test: `tests/test_conflict_gate.py`

**Interfaces:**
- Consumes: `detect_version_families`；`ModuleSkeleton`（`rel_path`, `symbols` 列表——读 `code_scanner.py` 实际字段名，按现网 `to_text()` 能扫到的类/函数名）
- Produces:

```python
@dataclass
class DraftItem:
    title: str
    content: str
    content_type: str
    source_url: str  # posix
    tags: list[str]
    signal: str  # docs|code|config|git|conversation|rules|correlation

@dataclass
class ConflictDraft:
    conflict_type: str  # version | doc_code | incoherent
    left_source: str
    right_source: str
    reason: str
    hold_sources: list[str]
    recommended: str  # keep_item | keep_peer | coexist
    recommended_reason: str

@dataclass
class GateResult:
    hold_sources: set[str]
    conflicts: list[ConflictDraft]

def gate_drafts(
    drafts: list[DraftItem],
    skeletons: list[Any],
    project_root: Path,
) -> GateResult: ...
```

规则（宁缺毋滥）：
- version：`detect_version_families` + 标题规范化相同、路径不同、正文 Jaccard < 0.85 → hold 两侧 docs。
- doc_code：`signal==docs` 且内容用正则抓 `class Foo` / `` `Foo` `` / 驼峰标识，关联到某 skeleton（文件名 Jaccard≥0.5 或标题对 stem），标识不在该骨架文本里 → hold **文档 source**，`recommended=keep_peer`（对侧当代码现状，`right_source`=模块路径）。
- incoherent：两条草稿（或一条草稿 vs 传入的 extra_active，Task 8 用）key phrase ≥4 字且窗口极性相反。bootstrap 两侧都 hold。禁止族/允许族复用 `injector/conflict.py` 的 `_PROHIBITIVE` / `_PERMISSIVE`（从该模块 import，不要复制两套词表）。

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from rsi_boot.scanner.conflict_gate import DraftItem, gate_drafts

def test_versioned_filenames_hold_both(tmp_path: Path):
    (tmp_path / "foo-v1.0.md").write_text("# Foo\n\nold api\n", encoding="utf-8")
    (tmp_path / "foo-v1.1.md").write_text("# Foo\n\nnew api\n", encoding="utf-8")
    drafts = [
        DraftItem("Foo", "old api " * 20, "documentation", "foo-v1.0.md", ["signal:docs"], "docs"),
        DraftItem("Foo", "new api " * 20, "documentation", "foo-v1.1.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [], tmp_path)
    assert "foo-v1.0.md" in result.hold_sources
    assert "foo-v1.1.md" in result.hold_sources
    assert any(c.conflict_type == "version" for c in result.conflicts)

def test_doc_missing_class_holds_doc_only():
    from rsi_boot.scanner.code_scanner import ModuleSkeleton
    sk = ModuleSkeleton(rel_path="user.py", language="python", symbols=["User"])
    drafts = [
        DraftItem("User", "See class MissingThing in the service layer. " * 5,
                  "documentation", "docs/user.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [sk], Path("."))
    assert "docs/user.md" in result.hold_sources
    assert any(c.conflict_type == "doc_code" for c in result.conflicts)

def test_polarity_holds_both():
    drafts = [
        DraftItem("禁止：pydantic", "禁止使用 pydantic 作为入参。 " * 4,
                  "prohibition", "auto-a", ["signal:rules"], "rules"),
        DraftItem("API 用 pydantic", "允许使用 pydantic 校验请求体。 " * 4,
                  "convention", "docs/api.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [], Path("."))
    assert result.hold_sources >= {"auto-a", "docs/api.md"}
```

若 `ModuleSkeleton` 字段名不同，测试改为该 dataclass 的真实构造参数（读 `code_scanner.py`）。

- [ ] **Step 2: Run FAIL**

Run: `python -X utf8 -m pytest tests/test_conflict_gate.py -q`

- [ ] **Step 3: Implement `conflict_gate.py`**

- [ ] **Step 4: Run PASS**

Run: `python -X utf8 -m pytest tests/test_conflict_gate.py -q`

- [ ] **Step 5: Commit**

```text
feat: bootstrap 冲突门（版本/文码/极性）
```

---

### Task 4: 冲突落库、explain、resolve 扩展

**Files:**
- Modify: `src/rsi_boot/injector/conflict.py`
- Modify: `src/rsi_boot/api/tools/conflicts_tool.py`
- Test: `tests/test_conflict_explain.py`

**Interfaces:**
- Consumes: `ConflictDraft`；现有 `rule_conflicts` 表（不改 schema：对侧路径放 `user_rule_path`，家族哈希放 `user_rule_hash`，摘录放 `user_rule_excerpt`）
- Produces:

```python
async def persist_knowledge_conflicts(
    self, project_id: str, drafts: list[ConflictDraft],
    source_to_item_id: dict[str, str],
) -> int:
    """INSERT OR IGNORE；item_id = left_source 对应条目；user_rule_path = right_source。"""

async def explain(self, conflict_id: str) -> dict | None:
    """只读。返回 sides、impact、options，不 UPDATE。"""
```

`resolve`：`conflict_type in ("version", "doc_code", "incoherent")` 时允许 `keep_item` / `keep_peer` / `coexist`。`doc_code`/`incoherent` 的 keep_*：按 source_url 把对侧 `pending_review`/`active` 归档，保留侧若是 pending 则改为 active（日常新稿胜出时激活新稿）。`coexist`：两侧 pending→active，不归档。

`conflicts_tool`：`action` enum 加 `explain`；`conflict_id` 必填。

- [ ] **Step 1: Write the failing test**（用现有 bootstrap/runtime fixture 风格，参考 `tests/test_review_queue.py` 的 `build_runtime`）

```python
import pytest
from rsi_boot.bootstrap import build_runtime
from rsi_boot.scanner.conflict_gate import ConflictDraft

@pytest.mark.asyncio
async def test_explain_does_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        pid = rt.project_id
        # 插入两条 knowledge + 一条 open incoherent（直接 SQL 或 persist_knowledge_conflicts）
        ...
        before = await rt.db.connect()
        explained = await rt.conflict_detector.explain(conflict_id)
        assert explained["sides"]
        assert "keep_item" in {o["resolution"] for o in explained["options"]}
        # 再读 status 仍为 open
    finally:
        await rt.close()
```

把 `...` 写成完整插入（两条 `knowledge_items` + `persist_knowledge_conflicts`）。

- [ ] **Step 2: Run FAIL**

- [ ] **Step 3: Implement persist / explain / resolve 分支**

- [ ] **Step 4: Run** `python -X utf8 -m pytest tests/test_conflict_explain.py -q`

- [ ] **Step 5: Commit**

```text
feat: 知识冲突 explain 与 keep_* 裁决
```

---

### Task 5: bootstrap 写入车道 + run id + cap 隔离

**Files:**
- Modify: `src/rsi_boot/cli/bootstrap_command.py`
- Modify: `src/rsi_boot/scanner/incremental.py`（`ingest_document` 已有 `status`/`tags`）
- Modify: `src/rsi_boot/config/default.yaml`（cap 注释：仅 bootstrap_run_id；默认保持 50 或改为 500 **只警告**——实现为：超过 500 打 warning，**不 archived**）
- Test: `tests/test_bootstrap_lanes.py`

**Interfaces:**
- Consumes: `gate_drafts`；`uuid.uuid4().hex` → tag `bootstrap_run_id:<id>`
- Produces: 写库时 docs/code/config/git/correlation：source 不在 `hold_sources` 且 signal 属车道 A → `status=active`；conversation/rules → 恒 `pending_review`；hold 中的原文 → `pending_review`。`_enforce_review_cap` 改为只 SELECT `tags LIKE '%bootstrap_run_id%'`，并且 **不再 UPDATE archived**（函数可改名为检查+`report.review_queue_warning`）。把 run id 写入 `.rsi/bootstrap_run.json`：`{"latest": "<id>"}`。

车道 A 信号：`docs`（未 hold）、`code`、`config`、`git`、`correlation`（未 hold）。  
车道 B：`conversation`、`rules`。  
车道 C：hold_sources。

- [ ] **Step 1: Write the failing test**

```python
# 干净小仓：README + pyproject → knowledge status 全是 active（或仅规则种子 pending）
# 预置 10 条 source_url=auto-extract pending，bootstrap 后这 10 条仍 pending
# foo-v1.0.md + foo-v1.1.md → 这两条 source 的条目 pending，其它 active
```

完整写出 async `run_bootstrap` 调用，断言 SQL 计数。参考 `tests/test_bootstrap.py` 的 `_args`。

- [ ] **Step 2: Run FAIL**

- [ ] **Step 3: Wire bootstrap_command**

顺序：discover → 扫 docs 成 DraftItem 列表（先 slice 不写库）+ 其它摘要草稿 → `gate_drafts` → 再 `ingest_document(..., status=..., tags=[..., f"bootstrap_run_id:{rid}"])`。dry-run 不写库、不跑 gate（规格 §10）。

- [ ] **Step 4: Run** `python -X utf8 -m pytest tests/test_bootstrap_lanes.py tests/test_bootstrap.py tests/test_review_queue.py tests/test_knowledge_freshness.py -q`

Expected: 旧「pending 上限」测试若依赖 overflow archived，改为断言 cap 不再误伤 auto-extract；必要时改旧测试以匹配「A 直通、不再把文档 archived」。

- [ ] **Step 5: Commit**

```text
feat: bootstrap 无冲突原文直通并隔离审批帽
```

---

### Task 6: Markdown 学习报告

**Files:**
- Modify: `src/rsi_boot/scanner/report.py`
- Modify: `src/rsi_boot/cli/bootstrap_command.py`（`write_markdown` + 终端打印路径）
- Test: `tests/test_bootstrap_report_md.py`

**Interfaces:**
- Consumes: `BootstrapReport` 新字段：

```python
applied: dict[str, int]  # docs/code/config/git/correlation
applied_samples: dict[str, list[str]]  # 每类最多 15 标题
slice_stats: dict[str, int]
extracts: list[dict]  # title, source
conflicts: list[dict]  # type, left, right, reason
profile_summary: 已有
```

- Produces: `report.write_markdown(path: Path) -> None`，六段标题固定为 `## 画像` `## 直通生效` `## 切片统计` `## 抽取待确认` `## 冲突组` `## 下一步`。下一步文案必须含「下一次任务会在对话里弹出抉择」，不要把 CLI 写成唯一路径。

- [ ] **Step 1: 失败测试** 断言 md 含六段标题且含「对话里」。

- [ ] **Step 2–4:** 实现并跑 `tests/test_bootstrap_report_md.py tests/test_bootstrap_progress.py -q`

- [ ] **Step 5: Commit** `feat: 输出给人看的 bootstrap_report.md`

---

### Task 7: review_batch 过滤 + `rsi knowledge accept`

**Files:**
- Modify: `src/rsi_boot/services/knowledge_service.py` `review_batch`
- Modify: `src/rsi_boot/api/tools/knowledge_review_tool.py`（`all_pending` 默认 `exclude_bootstrap=True`）
- Create: `src/rsi_boot/cli/knowledge_accept.py`
- Modify: `src/rsi_boot/__main__.py`
- Test: `tests/test_knowledge_accept.py`

**Interfaces:**

```python
async def review_batch(
    self, project_id, approve, *,
    ids=None, content_type=None, include_archived=False,
    bootstrap_run_id: str | None = None,
    exclude_bootstrap: bool = False,
    source_url: str | None = None,
) -> dict: ...

async def accept_bootstrap_extracts(
    runtime, *, run_id: str | None, reject: bool, conflicts: str | None,
) -> dict:
    """conflicts: None | 'tend' | 'coexist'。只处理 tags 含 bootstrap_run_id 且 signal conversation|rules。"""
```

`accept`：读 `.rsi/bootstrap_run.json` 的 latest（或 `--run`）。无抽取 → 打印「本轮无抽取项」return 0。`--conflicts tend` 对本 run 的 open `version|doc_code|incoherent` 调 `recommended`（落在 excerpt 或另存：persist 时把 recommended 写入 `resolution_note` 前缀 `recommended:<id>`，accept 解析它）。

- [ ] **Step 1: 测试**
  - 同库：`auto-extract` pending + bootstrap_run 抽取 pending → `accept` 只放行后者。
  - `all_pending` approve 不放行带 `bootstrap_run_id` 的条目。

- [ ] **Step 2–4:** 实现 CLI：`knowledge accept [--reject] [--conflicts tend|coexist] [--run ID]`

- [ ] **Step 5: Commit** `feat: knowledge accept 只放行本轮 bootstrap 抽取`

---

### Task 8: 日常提取 vs 历史极性冲突

**Files:**
- Modify: `src/rsi_boot/learning/knowledge_extractor.py`
- Test: `tests/test_extract_incoherent.py`

**Interfaces:**
- Consumes: `gate_drafts`；把新草稿当 `DraftItem`，把 active 历史做成第二组 drafts（或给 `gate_drafts` 增加 `peers: list[DraftItem]`）。**采用 `peers` 参数**，bootstrap 调用不传；提取器传入 active convention/prohibition/experience。
- Produces: 命中 incoherent 时历史保持 `active`，新稿 `pending_review`，`persist_knowledge_conflicts`；`hold_sources` **不要**用来改历史 status。

```python
def gate_drafts(..., peers: list[DraftItem] | None = None) -> GateResult:
    # peers 只参与 incoherent 配对，不进入 hold_sources（除非 bootstrap 两侧都是 drafts）
```

提取器路径：`peers` 来自 SQL `status='active'`。冲突的 `hold_sources` 仅含新稿 source（`auto-extract`）。

- [ ] **Step 1: 测试** 已 active「允许使用 X」+ 新 prohibition「禁止 X」→ 新稿 pending、旧稿 active、存在 open incoherent。标题完全相同仍走合并、不开冲突。

- [ ] **Step 2–4:** 实现；跑 `tests/test_extract_incoherent.py` 及现有 extract 测试。

- [ ] **Step 5: Commit** `feat: 日常新稿与历史极性冲突开待审卡`

---

### Task 9: 对话内抉择（recall.decisions + sticky + 注入）

**Files:**
- Create: `src/rsi_boot/services/decision_queue.py`
- Modify: `src/rsi_boot/services/recall_service.py`
- Modify: `src/rsi_boot/api/tools/recall_tool.py`
- Modify: `src/rsi_boot/api/tools/conflicts_tool.py`（已有 explain）
- Modify: `src/rsi_boot/injector/targets.py`
- Test: `tests/test_decision_queue.py`、`tests/test_recall_decisions.py`

**Interfaces:**

```python
@dataclass
class DecisionCard:
    id: str
    kind: str  # daily_conflict | bootstrap_extract | bootstrap_conflict
    prompt: str
    options: list[dict]
    recommended: str
    recommended_reason: str
    sides: list[dict]
    impact: dict
    more_waiting: int

class DecisionQueue:
    def pick(self, cards: list[DecisionCard]) -> DecisionCard | None:
        """sticky 未关闭的 id 优先；被 suppress 的跳过。"""
    def mark_presented(self, decision_id: str) -> None: ...
    def suppress(self, decision_id: str, seconds: float = 86400) -> None: ...
    def close(self, decision_id: str) -> None: ...
```

优先级：`daily_conflict` > `bootstrap_conflict` > `bootstrap_extract`。每次 recall 最多 1 张。`bootstrap_extract` 的 id 用 `extract:<run_id>`，options：`approve` / `reject` / `skip`（skip → `suppress`，不调 review）。

`RecallService.recall` 返回值增加 `"decisions": [card.asdict()]` 或 `[]`。

`TOOL_DESCRIPTION` 必须含：有 `decisions` 时由你调用 `rsi_conflicts`/`rsi_knowledge_review`；用户要自动执行用 `recommended`；用户要展开则 `explain` 且不要关闭卡；不要让用户自己去终端跑 rsi。

`CursorRuleTarget.write` **每次**写出 `rsi-decisions.mdc`（`alwaysApply: true`），正文不超过 800 字，重复上述纪律，**不要**注入冲突条目正文。

- [ ] **Step 1: 测试**
  - sticky：present A 后 `pick([B, A])` 仍得 A。
  - suppress A 后 pick 到 B。
  - recall 有 open daily_conflict → `decisions[0].kind == "daily_conflict"`。
  - 工具描述字符串包含「不要让用户自己去终端」。
  - 注入产物含 `rsi-decisions.mdc`。

- [ ] **Step 2–4:** 实现。`resolve`/`accept` 成功后 `queue.close(id)`（RecallService 持有进程内 `DecisionQueue` 单例，挂在 `runtime.decisions`）。

- [ ] **Step 5: Commit** `feat: 召回携带决策卡并由 agent 落库`

---

### Task 10: 规格 §11 端到端补齐

**Files:**
- Test: `tests/test_bootstrap_apply_e2e.py`
- 不改产品行为，只补缺口用例。

覆盖：
1. 干净仓 → active>0，pending 仅为抽取（无 conversation/rules 则 0）。
2. 文码 Missing class → 文档 pending，代码骨架 active。
3. `review_queue_cap=2` + 10 条 auto-extract → bootstrap 后 10 条仍 pending。
4. accept 后抽取 active，auto-extract 不变。
5. recall decisions + explain 不改库。

- [ ] **Step 1–4:** 缺哪个补哪个，全绿。

Run: `python -X utf8 -m pytest tests/ -q`

Expected: 全量绿（当前基线约 381+）。

- [ ] **Step 5: Commit** `test: bootstrap 直通与对话抉择验收`

---

### Task 11: README

**Files:**
- Modify: `README.md` 快速上手第 4 步后补三句：无冲突原文直接生效；抽取/冲突在 Cursor 对话里由 agent 调工具确认；已用旧逻辑学过的项目需删 `.rsi/rsi.db*` 后重学。不要新开长文档。

- [ ] **Step 1:** 不写测试（文档）。
- [ ] **Step 2:** 改 README。
- [ ] **Step 3: Commit** `docs: 说明 bootstrap 直通与对话内确认`

---

## Spec coverage（自检）

| Spec | Task |
|------|------|
| §2 三车道 | 5 |
| §3 三类冲突 + keep_* | 3, 4 |
| §4 切片参数 | 1 |
| §5 md 报告六段 | 6 |
| §6 CLI accept | 7 |
| §7.2 隔离硬规则 | 5, 7 |
| §7.3 日常 vs 历史 | 8 |
| §7.4 watch 不改 | 约束，无任务 |
| §7.5 decisions/explain/sticky/自动执行 | 9 |
| §8 分桶画像 | 2 |
| §9 不清库自动提升 | 11 |
| §10 dry-run/无抽取 | 5, 7 |
| §11 测试表 | 10 + 各任务 |
| 展开追问不关卡 | 9 sticky + explain 只读 |

无 TBD。决议动词统一 `keep_item` / `keep_peer` / `coexist`（不用 keep-current 字符串）。
