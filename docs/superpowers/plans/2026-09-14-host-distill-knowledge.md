# 宿主蒸馏项目知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `rsi bootstrap` 改成只采集阅读包；删除 Jaccard 原文出门；宿主经 `rsi_learn pack_*` 蒸馏短知识；召回/search 按规格过滤；修 bug 教学低权建议并可加重。

**Architecture:** 采集 CLI 只写 `.rsi/state/reading-packs/`。理解与写知识留在宿主。共享 `is_harvest_doc` 过滤旧采集物。教学写正式目录，召回信封带 `role`/`suggestions[]`，`retrieved` 含案例 id。第一波必须删旧门，禁止先留 Jaccard。

**Tech Stack:** Python 3.12、pytest、PyYAML、现有 `Progress` / MCP stdio / `MemoryStore`。Windows：`python -X utf8 -m pytest …`；PowerShell 不用 `&&`，用 `;`。

**Spec:** `docs/superpowers/specs/2026-09-14-host-distill-knowledge-design.md`

## Global Constraints

- 零 Key：`rsi` 包不打 Chat Completions，不配 LLM API Key。
- 禁止分词/Jaccard/极性窗/骨架标识符点名作出门或「算不算知识」。`conflict_gate.py` 整文件删除，不得 no-op、不得 P1 复活。
- 知识点自包含：召回/search/冲突只读 YAML，不打开 `refs`。
- 旗标名、exit 2 文案不变：`必须指定 --host-judge 或 --local-judge`。
- 不新增 MCP **工具名**。`pack_list` / `pack_open` / `pack_done` 挂在 `rsi_learn`。
- `wipe --yes` 必须删 `.rsi/state/`（含 reading-packs）。禁止只清 `memory/` 留 `done` 包。
- 蒸馏条默认 `pending_review`；批准后才进召回。search 能见 pending 蒸馏条。旧采集物召回与 search 都排除。
- teaching/gene 写正式目录；`low_confidence` 只打标记。空 `error_signature` / 缺 `wrong_action` / 缺 `correct_fix` → `invalid`。
- +2 只认「上次该 id 的 `teach_record` 之后」的 `retrieved`。
- `git_fix` 的 `fix`/`bug` 按词边界或 `fix:` 前缀，不要当任意子串。
- search 纳入**全部** pending 短知识（含无 `signal:distilled` 的「记下来」），实现比「仅本 run」简单。
- bootstrap **禁止**再调用 `ingest_document` / `archive_missing_signals` / `reconcile_scope`（不得因文件列表归档旧 YAML）。`persist_knowledge_conflicts` 删除。`profiles.upsert` 可留。`conflict_detector.scan()` 可留（手写规则对账）；旧采集物噪音在 Task 7 用 `is_harvest_doc` 消掉。
- `knowledge.add`：校验失败返回 `{"status":"error","code":"invalid","message":...}`；成功返回 id 字符串。MCP/CLI 见 `status=error` 原样传出，不得再包一层 success。
- `rsi-relearn` 是 `.rsi/memory/skills/` 里的正式 `type=skill`。bootstrap upsert，`rsi_recall.skills` **只**读 `store.list_official("skill")`。禁止写或扫描 `.cursor/skills`、`.claude/skills`。
- 阅读包磁盘路径：`store.rsi_dir / "state" / "reading-packs"`（即 `<root>/.rsi/state/reading-packs/`）。
- TDD：先红后绿。叙述走 `t(key, lang)`。提交只在执行本计划且用户未禁止时按步做；PowerShell 不用 `git commit -m "$(cat <<'EOF'")`，用 `git commit -m "feat: …"`。
- 存量：每波结束 `python -X utf8 -m pytest tests` 全绿。禁止 skip 挂起已删符号的测试。删 `conflict_gate.py` 的提交必须同时改完所有引用（Task 5a+5b 可两提交，但 5a 删文件前先改测试，或 5a 只改测试、5b 同提交删文件+重写 CLI）。

## 三波合入

| 波 | Tasks | 合入后可验证 |
|----|-------|----------------|
| A 采集与删除 | 1–6（含 5a/5b） | 只写阅读包；无 Jaccard；无原文知识；报告无 `judge_*`；注入已是阅读包循环 |
| B 编排与过滤 | 7–10 | `pack_*`、catalog 只读 memory skill、`knowledge_add` 硬顶、召回/search 过滤 |
| C 教学与信封 | 11–13 | 权重/合并/正式目录、`role`/`suggestions[]`、`retrieved` 含案例 id |

---

## File map

**Create**

- `src/rsi_boot/scanner/git_fix.py` — `is_fix_subject`、`list_fix_commits`
- `src/rsi_boot/scanner/reading_packs.py` — 包 IO、指纹、分包、`unlink_host_judge_queue`、upsert `rsi-relearn` 到 MemoryStore
- `src/rsi_boot/memory/harvest.py` — `HARVEST_TITLES`、`HARVEST_TAGS`、`is_harvest_doc`
- `src/rsi_boot/learning/reading_pack_actions.py` — `pack_list` / `pack_open` / `pack_done`
- `src/rsi_boot/scanner/relearn_skill.md` — 技能正文模板（写入 `.rsi/memory/skills/`，不写 IDE 目录）
- `tests/test_git_fix.py`
- `tests/test_reading_packs.py`
- `tests/test_harvest_filter.py`
- `tests/test_learn_packs.py`

**Modify**

- `src/rsi_boot/cli/bootstrap_command.py` — 采集只写包；删 DraftItem/gate/队列写入
- `src/rsi_boot/scanner/report.py` — 删 `judge_candidates` 等四字段；加 `pack_count` / `pack_omitted_sources`
- `src/rsi_boot/injector/conflict.py` — **删除** `persist_knowledge_conflicts`；去掉 `ConflictDraft` 导入。`scan()` 保留。
- `src/rsi_boot/injector/targets.py` — `_DECISIONS_BODY` 在 **Task 6** 换成阅读包循环（波 A 就改，不等 Task 10）
- `src/rsi_boot/api/tools/learn_tool.py` — 三个 pack action
- `src/rsi_boot/services/knowledge_service.py` — 1500 硬顶、四标题拒绝、distilled 必带 run id、search 可见集
- `src/rsi_boot/services/recall_service.py` — 主集过滤、信封、`retrieved`
- `src/rsi_boot/learning/teaching.py` — 校验、权重、合并、正式目录
- `src/rsi_boot/learning/promote.py` — `record_count>=3` 或 `weight>=8`
- `src/rsi_boot/ux/messages.py` — pack / teach / 采集阶段文案
- `src/rsi_boot/cli/wipe_command.py` — 仅当现网已清 `state/` 则加测试、不改行为

**Delete**

- `src/rsi_boot/scanner/conflict_gate.py`
- `tests/test_conflict_gate.py`

**Rewrite or delete tests that import 已删符号**

- `tests/test_bootstrap_judge_flags.py` — 旗标 + 阅读包 + 无队列
- `tests/test_bootstrap_apply_e2e.py`、`tests/test_bootstrap_lanes.py`、`tests/test_bootstrap.py` — 不再断言骨架入库
- `tests/test_bootstrap_report_md.py` — 无 `judge_queue_path`
- `tests/test_extract_incoherent.py` — 去掉 `gate_drafts`；改为 `knowledge_search` + 手写冲突或不测 Jaccard
- `tests/test_version_conflict.py`、`tests/test_conflict_explain.py`、`tests/test_knowledge_accept.py` — 不再 `from conflict_gate import ConflictDraft`
- `tests/test_knowledge_freshness.py` — 不再假设聚合摘要入库
- `tests/test_learn_tool.py` — 低置信不再进 `teaching-cases/pending`
- `tests/test_recall.py` / `tests/test_recall_files.py` — 信封与过滤
- `tests/test_closeout_inject.py` / `tests/test_recall_decisions.py` — 注入含 `pack_list`，无队列句

**Keep（不要抄回 Jaccard）**

- `injector/conflict.py` 手写规则 vs 短知识
- `scanner/version_conflict.py` — 只给阅读包 `hint: version-family`
- `rsi_conflicts` MCP

---

### Task 1: Git fix 标题匹配与提交列表

**Files:**
- Create: `src/rsi_boot/scanner/git_fix.py`
- Test: `tests/test_git_fix.py`
- Modify later: `src/rsi_boot/scanner/git_analyzer.py` 只被本模块调用 `git log`，不要改 `analyze_git` 的画像语义

**Interfaces:**
- Produces:
```python
def is_fix_subject(subject: str) -> bool: ...
def list_fix_commits(project_root: Path, max_commits: int = 500) -> list[dict[str, Any]]:
    # [{"hash": "abc1234", "message": "...", "files": ["src/a.py"]}, ...]
    # 无 .git / git 失败 → []
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_git_fix.py
from rsi_boot.scanner.git_fix import is_fix_subject

def test_fix_word_not_substring():
    assert is_fix_subject("fix token refresh")
    assert is_fix_subject("fix: npe")
    assert is_fix_subject("hotfix login")
    assert is_fix_subject("revert bad deploy")
    assert is_fix_subject("修复登录空指针")
    assert is_fix_subject("回滚错误发布")
    assert is_fix_subject("缺陷：会话丢失")
    assert not is_fix_subject("prefix the path")
    assert not is_fix_subject("docs: update readme")
    assert not is_fix_subject("chore: bump")
```

- [ ] **Step 2: Run** `python -X utf8 -m pytest tests/test_git_fix.py::test_fix_word_not_substring -v`  
  Expected: FAIL import or assert

- [ ] **Step 3: Implement** `is_fix_subject`

```python
import re
_FIX_WORD = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:fix|hotfix|bug|revert)(?:[^a-z0-9]|$)|"
    r"(?i)^fix(\(.+?\))?!?:|"
    r"修复|缺陷|回滚"
)
def is_fix_subject(subject: str) -> bool:
    return bool(_FIX_WORD.search((subject or "").strip()))
```

`list_fix_commits`：`git log --all -n{max} --name-only --format=%h%x00%s`（或等价），过滤 `is_fix_subject`，files 为随后的路径行。失败返回 `[]`。

- [ ] **Step 4: Add test** 在临时 git 仓库提交 `fix: a` 与 `docs: b`，断言只返回 fix 那条。再跑 `python -X utf8 -m pytest tests/test_git_fix.py -v` Expected: PASS

- [ ] **Step 5: Commit** `feat: match git fix subjects by word boundary`

---

### Task 2: 阅读包 IO 与指纹

**Files:**
- Create: `src/rsi_boot/scanner/reading_packs.py`（本 task 先 IO，分包在 Task 3）
- Test: `tests/test_reading_packs.py`

**Interfaces:**
```python
PACKS_DIRNAME = "reading-packs"
MAX_SOURCES_PER_PACK = 40
MAX_PACKS = 80

def packs_dir(rsi_dir: Path) -> Path: ...
def source_fingerprint(kind: str, path: str = "", hash: str = "", heading: str = "") -> str:
    """sha256 hex of kind + path + hash + heading."""
def pack_fingerprint(sources: list[dict]) -> str:
    """sha256 of sorted member fingerprints."""
def write_packs(rsi_dir: Path, *, bootstrap_run_id: str, packs: list[dict], force: bool = False) -> int:
    """
    packs 项: {id, domain, title, sources: [ {kind, path?, heading?, hash?, message?, files?, hint?} ]}
    无 force：旧 index 里 fingerprint 相同且 status in (done, skipped) 的包保留 status。
    force：全部 status=pending。
    原子写：tmp 再 replace index.yaml 与各 <id>.yaml。
    返回写入的 pack 数（= len(packs)）。
    omitted 路径列表由 build_packs 返回，bootstrap 填 report.pack_omitted_sources，不要塞进 write_packs。
    """
def load_index(rsi_dir: Path) -> dict: ...  # 无目录 → {"packs": []}
def load_pack(rsi_dir: Path, pack_id: str) -> dict | None: ...
def set_pack_status(rsi_dir: Path, pack_id: str, status: str, reason: str = "") -> dict:
    # 非法 id → {"status": "error", "code": "not_found"}
    # 已是 done/skipped 再设相同终态 → 幂等 success
def unlink_host_judge_queue(rsi_dir: Path) -> None:
    p = rsi_dir / "host_judge_queue.json"
    if p.is_file():
        p.unlink()
```

- [ ] **Step 1: 测试** `write_packs` 后存在 `index.yaml`；改源指纹后 `done` 变 `pending`；相同指纹保留 `done`；`force=True` 把 `done` 改回 `pending`；`set_pack_status` 未知 id → `not_found`；`unlink_host_judge_queue` 删文件。

- [ ] **Step 2: 跑红** `python -X utf8 -m pytest tests/test_reading_packs.py -v`

- [ ] **Step 3: 实现 IO**（YAML `safe_dump`/`safe_load`，utf-8）

- [ ] **Step 4: 跑绿**

- [ ] **Step 5: Commit** `feat: persist reading-pack index and fingerprints`

---

### Task 3: 按域分包

**Files:**
- Modify: `src/rsi_boot/scanner/reading_packs.py`
- Test: `tests/test_reading_packs.py`

**Interfaces:**
```python
def build_packs(
    *,
    docs: list[dict],      # {path, heading}
    code: list[dict],      # {path, heading?}  heading 可为短符号
    git_fix: list[dict],   # {hash, message, files}
    rules: list[dict],     # {path, heading}
    skills: list[dict],    # {path, name}
    conversations: list[dict],  # {path}
    version_hints: dict[str, str] | None = None,  # path → "version-family"
) -> tuple[list[dict], list[str]]:
    """返回 (packs, omitted_source_paths 至多 200 条)。
    单包 sources≤40，总包≤80。同一 path 或同一 hash 只进一个包。
    git_fix 按 files 主目录挂代码/文档包，否则域 git-fix。
    """
```

分域（规格 §4.1）：`rules` / `skills` / 文档一级目录 / `README*` 单独包 / 代码一或二级目录 / `conversation`。

`auto-extract` / `item:` 不要传入 `build_packs`。

- [ ] **Step 1: 测试** 41 个 docs 同目录 → 至少 2 包；同一 path 不出现在两包；81 包上限触发 omitted；git_fix 与代码 path 去重以 hash 为 git 源身份（代码 path 仍可在 code 源，git 源用 hash，不把 `files[]` 当第二包的 path 键）。

- [ ] **Step 2–4: 红 / 实现 / 绿**

- [ ] **Step 5: Commit** `feat: split reading sources into domain packs`

---

### Task 4: 报告字段与采集阶段名

**Files:**
- Modify: `src/rsi_boot/scanner/report.py`
- Modify: `src/rsi_boot/cli/bootstrap_command.py` 的 `_phase_names`（可先改阶段列表，采集循环在 Task 5）
- Modify: `tests/test_bootstrap_report_md.py`
- Modify: `src/rsi_boot/ux/messages.py` 若阶段名走 `t()`

**Produces:** `BootstrapReport` 删除 `judge_candidates` / `judge_omitted` / `judge_unresolved` / `judge_queue_path` 及一切读写。新增 `pack_count: int = 0`、`pack_omitted_sources: list[str] = field(default_factory=list)`。保留 `judge`。`knowledge_written` 采集后恒 0。`_format_judge_line` 改为报告 `pack_count`，host/local 各一句（local：在当前宿主按 `rsi-relearn` 继续，不点名 IDE）。增量 `harvest_warning` 字段本 task 加上并渲染；**赋值在 Task 7**（那时才有 `is_harvest_doc`）。

- [ ] **Step 1: 改 `test_bootstrap_report_md.py`**：构造报告不再传 `judge_queue_path`；断言无 `host_judge_queue`；有 `pack_count`。

- [ ] **Step 2: 跑该文件应红**

- [ ] **Step 3: 改 dataclass 与 `render_terminal` / JSON**

- [ ] **Step 4: 绿**

- [ ] **Step 5: Commit** `fix: drop bootstrap judge_* report fields`

---

### Task 5a: 拆掉旧门引用（先改测试与 conflict.py）

本 task **不**重写 `run_bootstrap` 主体。目标：树里不再能 import `conflict_gate`，测试不再依赖 Jaccard / 原文队列。

**Files:**
- Delete: `src/rsi_boot/scanner/conflict_gate.py`、`tests/test_conflict_gate.py`（与 5b 同一次提交更稳；若先删文件，5a 必须同时改完下列测试，否则全量红）
- Modify: `src/rsi_boot/injector/conflict.py` — 删除 `persist_knowledge_conflicts` 及 `ConflictDraft` TYPE_CHECKING 导入。`scan()` 留下。
- Modify: File map「Rewrite」里所有 import `conflict_gate` / `_write_host_judge_queue` / `gate_drafts` / `ConflictDraft` 的测试
- 删除 `bootstrap_command` 里 `_write_host_judge_queue` 及对 `gate_drafts` / `persist_knowledge_conflicts` 的调用（可暂留空阶段，5b 换成写包）。**禁止**留下空函数叫这些名字。

`test_extract_incoherent.py`：删 `gate_drafts` 用例，或改为两条已落盘短知识 id 手写 open 冲突行。  
`test_version_conflict.py` / `test_conflict_explain.py` / `test_knowledge_accept.py`：改用 conflict **行 dict** 或 MCP 写入，禁止 `from conflict_gate import ConflictDraft`。  
`test_bootstrap_judge_flags.py`：先删 `_write_host_judge_queue` 单测；host/local 行为断言放到 5b（本 task 可 `@pytest.mark` **不要** skip 挂起——5b 同一波必须接上，5a 单独合入前用「断言尚未写包」只留 exit 2 / parser）。

- [ ] **Step 1:** `rg conflict_gate|_write_host_judge_queue|gate_drafts|ConflictDraft src tests` 列出清单，按文件改到零引用（规格文档除外）。

- [ ] **Step 2:** `python -X utf8 -m pytest tests/test_conflict_explain.py tests/test_version_conflict.py tests/test_knowledge_accept.py tests/test_extract_incoherent.py tests/test_bootstrap_judge_flags.py -v`

- [ ] **Step 3: Commit** `refactor: remove conflict_gate imports and persist_knowledge_conflicts`

若 5a 单独提交时 `run_bootstrap` 已不能跑通 host-judge，**不要单独 push 波 A**；紧接着做 5b。

---

### Task 5b: `run_bootstrap` 只采集阅读包

**Files:**
- Modify: `src/rsi_boot/cli/bootstrap_command.py`（重写非 dry-run 主体）
- Modify: `tests/test_bootstrap_judge_flags.py`、`tests/test_bootstrap_apply_e2e.py`、`tests/test_bootstrap_lanes.py`、`tests/test_bootstrap.py`、`tests/test_knowledge_freshness.py`

**`run_bootstrap` 锁死：**

1. 旗标校验不变。
2. 阶段：扫描文件树 → 文档索引（路径+一级标题，不 `slice_document` 入库）→ 代码索引（路径+可选短符号，失败只留路径）→ Git fix（`list_fix_commits`）→ 规则与技能 → 分包。禁止「冲突检测 0/N」。
3. `packs, omitted = build_packs(...)`；`write_packs(..., force=args.force)`；`report.pack_count = len(packs)`；`report.pack_omitted_sources = omitted[:200]`。
4. `knowledge_written = 0`。不 `store.write` 任何 convention/documentation/prohibition。`rsi-relearn` skill 由 Task 6 的 `write_relearn_skill` 写入，不算蒸馏知识。
5. **禁止**调用 `ingest_document` / `archive_missing_signals` / `reconcile_scope`（旧知识 YAML 保持不动）。
6. **禁止**调用 `persist_knowledge_conflicts`（5a 已删）。
7. `unlink_host_judge_queue(rsi_dir)`。
8. `profiles.upsert(build_profile(...))` **可以留**。
9. `conflict_detector.scan(project_id)` **可以留**（手写规则 vs 已有短知识）。
10. `--local-judge` 与 `--host-judge` 采集相同；local stdout 多一句「在当前宿主按 rsi-relearn 继续」，不点名 IDE。都不灌原文冲突。两者都 upsert 技能条。
11. `--dry-run`：打印将生成的包数/域，不写盘。
12. 采集 IO 失败 exit 1。
13. `detect_version_families` 只填 `version_hints`，不写知识。
14. 技能文件：调用 `write_relearn_skill`（Task 6 实现；5b 可先调已有函数，若尚未落地则 5b 与 6 同提交）。

文档一级标题：读文件前 2KB，匹配第一个 `^#\s+`。

`harvest_warning` **本 task 先不要算**（`is_harvest_doc` 在 Task 7）。报告字段可留空串。

- [ ] **Step 1: 改 `tests/test_bootstrap_judge_flags.py`**
  - 保留 exit 2 两例与 parser。
  - host-judge：无 `host_judge_queue.json`；存在 `.rsi/state/reading-packs/index.yaml` 且 `packs`；`memory/` 无四类采集标题；报告 `knowledge_written==0`、`judge==host`、无 `judge_queue_path`。
  - local-judge：有包、无本 run 蒸馏知识（允许 `rsi-relearn` skill）、无队列。
  - `--force`：先手写 done index，跑完全部 `pending`。
  - 中文「修复 xxx」git commit → 某包 `kind: git_fix`（`git init`）。
- [ ] **Step 2:** `test_bootstrap_apply_e2e.py` 改为断言阅读包 sources 含文档 path，无 chunk YAML。
- [ ] **Step 3:** `python -X utf8 -m pytest tests/test_bootstrap_judge_flags.py tests/test_bootstrap_report_md.py tests/test_reading_packs.py tests/test_git_fix.py -v` 绿后 `python -X utf8 -m pytest tests`。
- [ ] **Step 4: Commit** `feat: bootstrap writes reading packs only`

---

### Task 6: rsi-relearn 技能 + 注入菜谱 + wipe 覆盖阅读包

波 A 必须改注入，避免合入后菜谱仍写「每批 200 resolve」而已删队列。

**Files:**
- Create: `src/rsi_boot/scanner/relearn_skill.md`（包内模板，不是项目 `.cursor` 文件）
- Modify: `reading_packs.write_relearn_skill(store) -> bool`（写失败返回 False，采集仍成功）
- Modify: `bootstrap_command` 成功时调用（host 与 local 都调；若 5b 已接上则只补测试）
- Modify: `src/rsi_boot/injector/targets.py` `_DECISIONS_BODY`
- Modify: 收工纪律块（`test_closeout_inject.py` 断言处）：修完用户可见 bug（成功也算）必须 `teach_record`，lesson 含 `wrong_action`+`correct_fix`
- Modify: `tests/test_wipe.py` — 种 `state/reading-packs/index.yaml`，wipe 后不存在；顺带断言 wipe 后无 `memory/skills` 的 rsi-relearn
- Modify: `tests/test_recall_decisions.py`、`tests/test_closeout_inject.py`
- Test: `tests/test_reading_packs.py` 加 upsert 技能用例

**`write_relearn_skill` 锁死：**
- dest = `official_dir(store.rsi_dir, "skill")` / `memory_filename(...)`
- `type=skill`，`status=active`，`payload.name="rsi-relearn"`，`title` 可用 `rsi-relearn`，`description` 一句「重新学习时按阅读包蒸馏短知识」
- `content` = 模板全文
- 已有 `payload.name=="rsi-relearn"` 的 official skill → 覆盖同一 id / 同一文件，不新建
- **禁止**创建 `.cursor/skills/**` 或 `.claude/skills/**`

**技能正文必须含（规格 §4.5）：** wipe（清包）→ `rsi bootstrap --consent --host-judge` → `pack_list` / `pack_open` / 读源 / `rsi_knowledge_search` / `rsi_knowledge_add` / `pack_done` → `rsi_knowledge_review` 批准本 run → 未批准不得声称召回可用。禁止 Jaccard、禁止全文入库。一包 1–20 条，写不出 `skipped`+原因。不点名 Cursor / Codex / Claude。

**`_DECISIONS_BODY`：** 与技能同一顺序。全文不得再出现 `host_judge_queue.json` 或「每批 200 resolve」。保留：漏旗标加 `--host-judge`；`rsi_conflicts` 只处理短知识 id。不点名某一家 IDE。

- [ ] **Step 1:** 断言 `.rsi/memory/skills/` 下有 `payload.name==rsi-relearn` 的 YAML；`content` 含 `pack_list`；项目**无** `.cursor/skills/rsi-relearn/`；wipe 后无 `reading-packs` 且无该 skill；注入块含 `pack_list` / `rsi-relearn`。
- [ ] **Step 2:** `python -X utf8 -m pytest tests/test_reading_packs.py tests/test_wipe.py tests/test_recall_decisions.py tests/test_closeout_inject.py -v`
- [ ] **Step 3: Commit** `feat: upsert rsi-relearn skill into memory store`

**波 A 门禁：** 源码与测试无 `gate_drafts`、`_peer_sim`、`_write_host_judge_queue`。`python -X utf8 -m pytest tests` 全绿。可单独 PR。

---

### Task 7: 旧采集物识别 + 召回/search 可见集

**Files:**
- Create: `src/rsi_boot/memory/harvest.py`
- Modify: `src/rsi_boot/services/recall_service.py` — `_store_search_docs`
- Modify: `src/rsi_boot/services/knowledge_service.py` — `_search_store`（及 list_pending 并入 search 语料）
- Modify: `src/rsi_boot/injector/conflict.py` — 手写规则对账跳过 `is_harvest_doc`（同一函数）
- Test: `tests/test_harvest_filter.py`；补 `tests/test_recall.py` / knowledge search 测

**Interfaces:**
```python
HARVEST_TITLES = frozenset({
    "代码骨架摘要", "项目配置与规范摘要", "Git 历史分析", "跨信号关联图谱",
})
HARVEST_TAGS = frozenset({
    "signal:docs", "signal:doc-index", "signal:code",
    "signal:config", "signal:git", "signal:correlation",
})

def is_harvest_doc(doc) -> bool:
    tags = set(doc.tags or [])
    if "signal:distilled" in tags:
        return False
    if (doc.title or "") in HARVEST_TITLES:
        return True
    return bool(tags & HARVEST_TAGS)
```

**召回知识主集：** `status==active` 且 type 为 prohibition/convention/documentation；`not is_harvest_doc`。pending 蒸馏条不进。teaching/gene/episode 不走 harvest 排除。

**search：** 语料 = 召回知识主集 ∪ 全部 pending 短知识（prohibition/convention/documentation，含 `signal:distilled`）∪ 非 harvest 的 pending。排除 harvest。不读 `refs` 文件。teaching/gene 不要求出现在 search。

bootstrap 增量结束时：若 `store.list_official()` 里仍有 `is_harvest_doc`，设 `report.harvest_warning` 为条数（或「库存仍有 N 条旧采集物」），**不**删除那些 YAML。

- [ ] **Step 1: 单测** 四个标题排除；`signal:code` active 排除；同条 `signal:distilled`+`signal:docs` 且 active **纳入召回**；pending distilled **不召回、能 search**；无 distilled 的 pending 约定也能 search。
- [ ] **Step 2:** `python -X utf8 -m pytest tests/test_harvest_filter.py -v` 先红
- [ ] **Step 3:** 改 `_store_search_docs` / `_search_store` / `scan` 跳过 harvest；bootstrap 填 `harvest_warning`
- [ ] **Step 4:** 绿后 Commit `feat: exclude harvest docs from recall and search`

---

### Task 8: knowledge_add 硬顶与蒸馏标签

**Files:**
- Modify: `src/rsi_boot/services/knowledge_service.py` `_add_to_store` 开头校验
- Modify: knowledge add MCP/CLI 测试（现有 `tests/test_knowledge_*.py`）
- Modify: `src/rsi_boot/ux/messages.py` — `KNOWLEDGE_TOO_LONG` / `KNOWLEDGE_HARVEST_TITLE` / `KNOWLEDGE_DISTILL_TAGS`

**规则：**
- `len(content) > 1500` → 不写盘。`KnowledgeService.add` 返回 `{"status":"error","code":"invalid","message": t("KNOWLEDGE_TOO_LONG", ...)}`。成功路径仍返回 **id 字符串**（现网兼容）。
- `src/rsi_boot/api/tools/knowledge_add_tool.py`：`result` 若是 `dict` 且 `status=="error"` → **原样 return**，禁止 `{"status":"success", **result}`。
- CLI `knowledge add` 同一判断，错误走 stderr + 非 0。
- `title` 精确四采集标题 → invalid（`KNOWLEDGE_HARVEST_TITLE`）。
- tags 含 `signal:distilled` 但无 `bootstrap_run_id:` 前缀 → invalid（`KNOWLEDGE_DISTILL_TAGS`）。
- tags 含 `signal:distilled` → **一律** `pending_dir`，忽略「缺省 active」。
- 短于 80 字合法。

- [ ] **Step 1:** 测试超 1500、四标题、缺 run id、短禁止项成功、distilled 落 pending；MCP handle 对 error dict 不包 success。
- [ ] **Step 2:** 新测放 `tests/test_knowledge_files.py`（或新建 `tests/test_knowledge_add.py`）。`python -X utf8 -m pytest tests/test_knowledge_files.py -v` 先红
- [ ] **Step 3–4:** 实现并绿
- [ ] **Step 5: Commit** `feat: reject oversized and harvest-titled knowledge adds`

---

### Task 9: `rsi_learn` pack_* 

**Files:**
- Create: `src/rsi_boot/learning/reading_pack_actions.py`
- Modify: `src/rsi_boot/api/tools/learn_tool.py` — schema `action` 描述加上三个 pack；`handle` 分发
- Modify: `src/rsi_boot/ux/messages.py` `TOOL_DESC["learn"]` 与 `LEARN_NEED_ACTION`
- Test: `tests/test_learn_packs.py`
- **不要**加 CLI 子命令

**Produces:**
```python
# 包目录 = Path(store.rsi_dir) / "state" / "reading-packs"
def pack_list(store) -> dict:  # {status, data:{total,done,pending,skipped,packs:[...]}}
def pack_open(store, pack_id: str) -> dict
def pack_done(store, pack_id: str, status: str = "done", reason: str = "") -> dict
```
无目录：`pack_list` success，空列表，message 提示先 bootstrap。禁止到 `memory/` 下找包。

- [ ] **Step 1: 测试** 空目录不抛；open 未知 → `not_found`；done 幂等；list 计数正确。

- [ ] **Step 2–4**

- [ ] **Step 5: Commit** `feat: add rsi_learn pack_list open and done`

---

### Task 10: 召回技能目录只读 MemoryStore

注入与 upsert 已在 Task 6。本 task 锁死 catalog 来源，防止以后又去扫 IDE 目录。

**Files:**
- Modify: `src/rsi_boot/services/recall_service.py` `_store_skill_catalog`（现网已是 `list_official("skill")`；**不要**加文件系统扫描）
- Test: `tests/test_recall.py` 或 `tests/test_reading_packs.py`

**锁死：**
- catalog **只**来自 `store.list_official("skill")` 且 `status==active`：`name`=`payload.name` 或 `title`，`description`，`path`。
- **禁止** `rglob` / 遍历 `.cursor/skills`、`.claude/skills`、`config/skills`。
- Task 6 写入后，`rsi_recall.skills` 含 `name=="rsi-relearn"`。
- 负例：只在项目放 `.cursor/skills/ghost/SKILL.md`、`.rsi` 里没有对应 skill YAML → catalog **不含** `ghost`。

- [ ] **Step 1:** 用 `write_relearn_skill` 后 recall 含 `rsi-relearn`；再造 ghost SKILL.md，catalog 不含 ghost。
- [ ] **Step 2:** `python -X utf8 -m pytest tests/test_recall.py -k skill -v`（或新测）先红若有人误加扫描
- [ ] **Step 3–4:** 保持 MemoryStore-only；有扫描则删掉
- [ ] **Step 5: Commit** `test: recall skill catalog stays on memory store`

**波 B 门禁：** `python -X utf8 -m pytest tests` 全绿。可单独 PR。

---

### Task 11: teach_record 校验、合并、权重、正式目录

**Files:**
- Modify: `src/rsi_boot/learning/teaching.py`
- Modify: `src/rsi_boot/learning/gene_map.py` `find_duplicate`（已有 signature+failure_type）
- Modify: `src/rsi_boot/learning/promote.py` 候选条件加上盘上 `record_count>=3` 或 `weight>=8`
- Modify: `src/rsi_boot/ux/messages.py` — `TEACH_NEED_WRONG` / `TEACH_NEED_SIGNATURE`（可复用或新 key）
- Modify: `tests/test_learn_tool.py`、`tests/test_cli_learn.py`

**`teach_record` 锁死：**

1. `wrong_action` / `correct_fix` / 去空白 `error_signature` 任一空 → `invalid`。
2. `find_duplicate` 命中则更新该 teaching+gene（同 id），不新建 YAML。
3. 首次 agent：`weight=2`，`record_count=1`，`recall_hits=0`，`recorded_at=utc`。gene.payload 与 teaching.payload.fix_and_learn 都写这些字段。
4. 再次：`record_count+=1`。扫 `iter_events` 中 `kind==recall` 且 `retrieved` 含该 id 且 `ts` > 旧 `recorded_at` → 有则 `weight=min(10, weight+2)` 并刷新 `recall_hits` 累计；无则只追加 `system_attempts`。然后更新 `recorded_at`。
5. `author=user`：`weight=max(current,10)`，之后只升不降。
6. 先 user 后 agent：不把 weight 打回 2。
7. **禁止** `teaching-cases/pending/`。`low_confidence` 写入 `payload.lesson.low_confidence=true`。
8. 旧 YAML 仅有 `hit_count`：当作 `record_count`。

- [ ] **Step 1: 改 `test_teach_record_pending_and_pattern_paths`**：低置信 path **不含** `teaching-cases/pending`。加：缺 wrong_action / 空 signature → invalid；同 signature 第二次无 recall 不加 weight；写入假 recall 事件后再 record → +2；user 后 agent weight≥10。

- [ ] **Step 2–4**

- [ ] **Step 5: Commit** `feat: ramp teaching weights after recall hits`

---

### Task 12: 召回信封与 retrieved

**Files:**
- Modify: `src/rsi_boot/services/recall_service.py`
- Test: `tests/test_recall.py` 或新用例放 `tests/test_harvest_filter.py`

**锁死：**
- `_case_payload` 增加 `weight`（gene.payload.weight 或 teaching fix_and_learn.gene_map_weight，默认 2）、`role`：`suggestion` if weight<8 and 非 user；`constraint` if weight≥8 or lesson.author==user。
- 顶层 `suggestions`：teaching+gene 中 role=suggestion，**按 id 去重，teaching 优先**。
- `prohibitions` / `items` 不进 suggestions。
- `retrieved` = prohibitions+items+teaching+gene+suggestions+episodes 的 id 去重（已含 teaching 则不必因 suggestions 再漏）。**必须含**返回的 teaching/gene/suggestion id。
- 不得因 weight=2 丢弃案例。

- [ ] **Step 1: 测试** 首次修 bug 召回：`role=suggestion`、`weight=2`、在 `suggestions`、不在 `prohibitions`、id 在 `retrieved`（读最近 `events.jsonl`）。weight=10：`constraint`、不在 suggestions。harvest active 不在 items。

- [ ] **Step 2–4**

- [ ] **Step 5: Commit** `feat: expose recall suggestions and retrieved case ids`

---

### Task 13: 全量回归与规格对照

**Files:** 无新功能。扫残留。

- [ ] **Step 1:** `rg -n "gate_drafts|_peer_sim|_write_host_judge_queue|host_judge_queue|每批 200" src tests` 仅允许 unlink 与历史规格文档。
- [ ] **Step 2:** `python -X utf8 -m pytest tests` 全绿。
- [ ] **Step 3:** 对照规格 §7 测试清单逐条打勾；缺的补测（短于 80 字 add、search pending、wipe packs）。
- [ ] **Step 4: Commit** `test: cover host-distill spec acceptance list`

---

## Spec coverage（自检）

| 规格 | Task |
|------|------|
| §3.2 / §6.1 采集阶段、旗标、dry-run、force | 3, 5b |
| §3.4 local 不写记忆不开冲突 | 5b |
| §3.7 / §4.3 指纹保留 done | 2, 5b |
| §4.1 包约束、git 中文、80/40 | 1, 3 |
| §4.2 / §4.9 / §8 合法知识、search、wipe | 6–8 |
| §4.5–4.6 技能、注入、pack_*、catalog | 6, 9, 10 |
| §4.7 / §10 报告与删除清单 | 4, 5a, 5b, 13 |
| §4.8 召回主集 + harvest_warning | 7, 12 |
| §3.5–3.6 教学权重信封 | 11–12 |
| §11 验收 1–6 | 5b, 7, 8（两分钟是目标不是硬失败） |

未纳入本计划（规格 P1）：语义再切域、采集符号更完整、归档旧采集物脚本、新 MCP 工具名、把 `rsi-relearn` 导出为宿主 `SKILL.md`。

## 实现时注意

- `architecture` 现网映射为 `documentation`（`LEGACY_TYPE_MAP`），蒸馏类型写 documentation 即可。
- `ConflictDraft` 删文件后，解释/accept 测试改为构造 conflict **行 dict** 或走 `rsi_conflicts` 写入。
- 不要实现「按 ref 回写 knowledge」API。
- 不要把 `incremental.ingest_document` 接到 watch；bootstrap 也不得再调用它。
- 5a 与 5b 不要拆成两个已发布的 origin 提交中间态（本地可以两提交，push 波 A 时两者都要在）。
