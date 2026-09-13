# 宿主判断 / 本地整条比对 — 冲突检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 废除极性词笛卡尔积；`rsi bootstrap` 必须显式 `--host-judge`（agent 对话）或 `--local-judge`（人类 CLI）；host 路径只出工作包（`.rsi/host_judge_queue.json`）由宿主模型当场分批裁决，local 路径按「同一约束对象 + 极性相反」整条比对。

**Architecture:** 先改纯函数（conflict_gate 删极性机 + 加 peer 分桶/本地裁决），再接 CLI 旗标与队列文件，然后扩 `rsi_conflicts`（list 分页/按 run 过滤 + resolve 批量 decisions），最后更新注入文案、报告字段与存量测试。rsi 进程永不调 LLM。

**Tech Stack:** Python 3.12、pytest、aiosqlite、现有 MCP 工具（不新增工具名）。Windows 上测试命令一律 `python -X utf8 -m pytest …`，PowerShell 不用 `&&`。

**Spec:** `docs/superpowers/specs/2026-09-13-host-judge-conflict-design.md`

## Global Constraints

- 零 Key：rsi 进程不调任何 LLM；语义判断只发生在宿主对话里。
- 不新增 MCP 工具名；`rsi_conflicts` 只扩 `list` 参数与 `resolve` 的 `decisions` 数组。
- 非 dry-run 且两个 judge 旗标都缺 → exit 2，stderr 固定句 `必须指定 --host-judge 或 --local-judge`；两个都给 → exit 2，stderr `不能同时使用 --host-judge 与 --local-judge`。
- 候选硬顶 10000：先保 version/doc_code，再按 Jaccard 距 0.85 的距离（越不像复制越优先）收 peer；截断只写报告，不视为已判。
- `--local-judge` 对 peer 灰色区「宁缺毋滥」：吃不准不当冲突。
- `--host-judge` 对 peer 只入包不裁决、不 hold；version/doc_code 仍按文件证据 hold。
- `--local-judge` 运行时删除 `.rsi/host_judge_queue.json`（若存在）。
- 日常提取（`knowledge_extractor._persist_incoherent_vs_actives`）继续走同一套 peer 分桶，本地裁决（无宿主）。
- 旧库极性叉乘留下的 `incoherent` 不自动清理；README 指引 `rsi wipe --yes` 重学。
- TDD：先红后绿。提交仅在用户要求或本计划执行时按步提交。
- 裁决映射不变：`keep_item` = 保留 `rule_conflicts.item_id` 一侧；`keep_peer` = 保留 `user_rule_path` 一侧；`coexist` = 两侧可用不再提醒。

---

## File map

| File | Responsibility |
|------|----------------|
| `src/rsi_boot/scanner/conflict_gate.py` | 删极性机；peer 分桶（标题桶 + 父目录+H1 桶）；`judge` 参数；本地对象互斥裁决；10000 硬顶 |
| `src/rsi_boot/cli/bootstrap_command.py` | 旗标校验（exit 2）；传 `judge`；写/删 `host_judge_queue.json`；报告 judge 字段 |
| `src/rsi_boot/__main__.py` | bootstrap 子命令加 `--host-judge` / `--local-judge` |
| `src/rsi_boot/injector/conflict.py` | `list_conflicts` 分页 + `bootstrap_run_id` 过滤；`resolve_batch` 批量裁决 |
| `src/rsi_boot/api/tools/conflicts_tool.py` | schema 加 `bootstrap_run_id`/`limit`/`offset`/`decisions`；handle 分发 |
| `src/rsi_boot/scanner/report.py` | `judge` / `judge_candidates` / `judge_unresolved` / `judge_queue_path` / `judge_omitted` 字段与渲染 |
| `src/rsi_boot/injector/targets.py` | `_DECISIONS_BODY` 扩写（≤800 字符）：重新学习必须 `--host-judge`、exit-2 重跑、吃完队列 |
| `README.md` | 快速上手改 `--local-judge`；说明 agent 路径用 `--host-judge` |
| `tests/test_bootstrap_judge_flags.py` | **新建**：旗标校验 + host 队列 + local 删队列 |
| `tests/test_conflicts_tool_batch.py` | **新建**：list 分页/run 过滤 + resolve decisions 批量 |
| `tests/test_conflict_gate.py` | 删极性用例，换 peer 分桶/本地裁决用例 |
| `tests/test_extract_incoherent.py` | 日常提取用例改到标题桶配对 |
| `tests/test_bootstrap.py` 等 8 个 `_args` | 默认 `local_judge=True`（见 Task 9 清单） |

---

### Task 1: CLI 旗标 `--host-judge` / `--local-judge` 与 exit 2 校验

**Files:**
- Modify: `src/rsi_boot/__main__.py`（bootstrap parser，约 193-209 行）
- Modify: `src/rsi_boot/cli/bootstrap_command.py`（`run_bootstrap` 开头，376 行后）
- Test: `tests/test_bootstrap_judge_flags.py`（新建）

**Interfaces:**
- Consumes: 现有 `run_bootstrap(args: argparse.Namespace) -> int`；`args.dry_run`。
- Produces: `args.host_judge: bool`、`args.local_judge: bool`（argparse store_true）。校验函数：

```python
def _validate_judge_flags(args: argparse.Namespace) -> Optional[str]:
    """返回错误文案（调用方打印 stderr 并 exit 2）；合法返回 None。"""
```

- 校验时机：`run_bootstrap` 内 `project_root` 存在性检查之后、`Progress().start` 之前。dry-run 豁免。错误文案必须是固定句（注入文案与测试都按字面匹配）：
  - 都缺：`必须指定 --host-judge 或 --local-judge`
  - 都有：`不能同时使用 --host-judge 与 --local-judge`

- [ ] **Step 1: Write the failing test**

```python
"""bootstrap 判断旗标：非 dry-run 必须恰好一个。"""
import argparse
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=False,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def _proj(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + "这是一段足够长的仓库原文。" * 40,
        encoding="utf-8",
    )
    return root


async def test_bootstrap_without_judge_flag_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 2
    assert "必须指定 --host-judge 或 --local-judge" in capsys.readouterr().err


async def test_bootstrap_with_both_judge_flags_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, host_judge=True, local_judge=True)) == 2
    assert "不能同时使用 --host-judge 与 --local-judge" in capsys.readouterr().err


async def test_bootstrap_dry_run_exempt_from_judge_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert not (root / ".rsi" / "manifest.json").exists()


def test_parser_accepts_judge_flags():
    from rsi_boot.__main__ import build_parser  # 现网 179 行已是 build_parser()
    parser = build_parser()
    args = parser.parse_args(["bootstrap", "--host-judge"])
    assert args.host_judge is True and args.local_judge is False
    args = parser.parse_args(["bootstrap", "--local-judge"])
    assert args.local_judge is True and args.host_judge is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_bootstrap_judge_flags.py -q`

Expected: FAIL（`_args` 里 `host_judge`/`local_judge` 属性虽在，但 `run_bootstrap` 不校验 → 前两个用例返回 0；parser 无两个旗标 → 第四个用例 SystemExit）

- [ ] **Step 3: Write minimal implementation**

`__main__.py` bootstrap parser 加：

```python
p_boot.add_argument("--host-judge", action="store_true",
                    help="对话内由宿主模型裁决冲突工作包（agent 用）")
p_boot.add_argument("--local-judge", action="store_true",
                    help="本地整条知识比对裁决冲突（人类 CLI 用）")
```

`bootstrap_command.py`：

```python
def _validate_judge_flags(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "dry_run", False):
        return None
    host = bool(getattr(args, "host_judge", False))
    local = bool(getattr(args, "local_judge", False))
    if host and local:
        return "不能同时使用 --host-judge 与 --local-judge"
    if not host and not local:
        return "必须指定 --host-judge 或 --local-judge"
    return None
```

`run_bootstrap` 在 `project_root` 检查后：

```python
judge_error = _validate_judge_flags(args)
if judge_error:
    print(judge_error, file=sys.stderr)
    return 2
```

（`Optional` 已在 typing 导入中则直接用，否则补导入。）

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_bootstrap_judge_flags.py -q`

Expected: 前三个用例 PASS；第四个在 parser 旗标加好后 PASS。

- [ ] **Step 5: Commit**

```text
feat: bootstrap 必须显式 --host-judge 或 --local-judge
```

---

### Task 2: conflict_gate 废除极性机，peer 分桶 + 本地整条裁决

**Files:**
- Modify: `src/rsi_boot/scanner/conflict_gate.py`（删约 100 行极性代码，加分桶与裁决）
- Test: `tests/test_conflict_gate.py`（删 5 个极性用例，换 6 个新用例）

**Interfaces:**
- Consumes: `correlation_engine._jaccard` / `_words`（沿用）；`injector.conflict._PROHIBITIVE`（沿用，反向 import 现状不动）。
- Produces:

```python
_PEER_JACCARD_MIN = 0.35
_PEER_JACCARD_MAX = 0.85   # 复用 _BODY_JACCARD_THRESHOLD 的值，不另起常量
_MAX_CANDIDATES = 10000

# GateResult 增加字段（默认 0，旧调用方不受影响）
@dataclass
class GateResult:
    hold_sources: set[str]
    conflicts: list[ConflictDraft]
    omitted_candidates: int = 0

def gate_drafts(
    drafts: list[DraftItem],
    skeletons: list[Any],
    project_root: Path,
    peers: list[DraftItem] | None = None,
    on_progress: Callable[[int], None] | None = None,
    on_match_start: Callable[[int], None] | None = None,
    include: Optional[Sequence[str]] = None,
    judge: str = "local",           # "local" | "host"
) -> GateResult: ...
```

- 删除：`_polar_phrase_map`、`_iter_incoherent_pairs`、`_incoherent_hit`、`_emit_incoherent`、`_candidate_phrases`、`_polarity`、`_find_phrase`、`_key_phrase`、`_GATE_PERMISSIVE`、`_MAX_BUCKET_PAIRS`、`_EXCERPT_WINDOW`、`_MIN_KEY_PHRASE_LEN`。
- 新增分桶（纯函数）：

```python
_MODAL_WORDS = (
    "禁止", "不要", "不得", "避免", "严禁", "一律不",
    "允许", "推荐", "优先", "可以",
    "never", "don't", "do not", "must not", "avoid", "always", "prefer",
)

def _peer_parent_key(draft: DraftItem) -> str | None:
    """父目录 + 一级标题 桶键；auto-extract / item: / 无路径来源返回 None（只进标题桶）。"""

def _strip_modals(text: str) -> str:
    """去语气词后取剩余词块（排序词集 join），作为约束对象近似。"""

def _constraint_object(draft: DraftItem) -> str:
    """约束对象 = 正文首个 `# ` 一级标题（否则条目标题）去语气词后的排序词集。
    粒度刻意停在标题级：本地路径是兜底，标题相同但首句对象不同的细分交 host 模型。"""

def _local_peer_conflict(left: DraftItem, right: DraftItem) -> ConflictDraft | None:
    """本地裁决：对象相同且极性相反 → ConflictDraft(incoherent, hold 两侧)；否则 None。"""
```

- 配对规则（`_detect_incoherent_conflicts` 重写）：
  1. 桶：`defaultdict(list)` 两份——`by_title[_norm_title(d.title)]`、`by_parent[_peer_parent_key(d)]`（None 跳过）。`_peer_parent_key` = `f"{parent_dir}|{一级标题}"`：父目录取 `source_url` 的 posix 父路径（无路径来源如 `auto-extract`/`item:`/裸文件名返回 None）；一级标题取 `content` 里首个 `# ` 行文本（无则退回 `title`）。drafts 与 `peers or []` 都进桶；peers 只作右侧（标记来源）。
  2. 桶内两两（同桶、来源不同、非同一对象）计算 `_body_jaccard(left.content, right.content)`，落在 `[0.35, 0.85)` 才成为候选对。同一对只出一次（用 `(id(a), id(b))` 排序去重，沿用旧手法）。
  3. `judge="local"`：每对过 `_local_peer_conflict`；命中 → hold 两侧 + `ConflictDraft(conflict_type="incoherent", recommended="coexist", recommended_reason="同一约束对象极性相反，需裁决口径")`；不命中 → 丢弃。drafts×peers 对只 hold draft 侧（peer 是历史 active，不动它），与旧 `_emit_incoherent(hold_right=False)` 语义一致。
  4. `judge="host"`：每对直接产 `ConflictDraft`（reason 写 `同桶近似条目（Jaccard {sim:.2f}），待宿主裁决`），**不 hold 任何一侧**，recommended 给 `coexist`。
  5. 候选总量（version + doc_code + peer）超 `_MAX_CANDIDATES`：peer 候选按 `0.85 - sim` 降序截断，被截数量计入 `GateResult.omitted_candidates` 并 `logger.warning`。
  6. 进度：入桶阶段 `on_progress(progress_offset + i + 1)`（沿用旧节奏）；`on_match_start(候选对数)` 在配对前回调；每处理一对 `on_progress(done)`。配对数 = 桶内两两组合数（在 Jaccard 过滤前估算即可，用于进度条分母）。

- [ ] **Step 1: Write the failing test**

`tests/test_conflict_gate.py`：删除 `test_polarity_holds_both`、`test_incoherent_pair_loop_reports_progress`、`test_usage_heading_is_not_permissive_conflict`、`test_prefer_is_still_permissive`、`test_oversized_polar_bucket_is_sampled_not_dropped`，新增：

```python
def _draft(title, body, source, signal="docs", ctype="convention"):
    return DraftItem(title, body, ctype, source, [f"signal:{signal}"], signal)


def test_plaintext_vs_encrypted_password_not_paired():
    """对象不同（明文 vs 加密密码）：标题/目录桶对不上，不成对。"""
    drafts = [
        _draft("密码存储", "禁止写入明文密码到数据库。" * 6, "docs/security.md"),
        _draft("密码存储", "允许写入加密密码到数据库。" * 6, "docs/crypto.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert not any(c.conflict_type == "incoherent" for c in result.conflicts)
    assert not result.hold_sources


def test_same_object_opposite_polarity_opens_incoherent_local():
    """同一约束对象（pydantic 入参）一禁一许 → 本地裁决开 incoherent 并 hold 两侧。"""
    drafts = [
        _draft("API 入参", "禁止使用 pydantic 作为入参。" * 6, "rules/a.md", signal="rules", ctype="prohibition"),
        _draft("API 入参", "允许使用 pydantic 校验请求体。" * 6, "docs/api.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    conflict = next(c for c in result.conflicts if c.conflict_type == "incoherent")
    assert conflict.hold_sources == ["rules/a.md", "docs/api.md"]
    assert result.hold_sources >= {"rules/a.md", "docs/api.md"}


def test_same_title_bucket_pairs_near_duplicates():
    """同一规范化标题 + 正文 Jaccard 在 [0.35, 0.85) → 成对（local 下无极性不冲突）。"""
    drafts = [
        _draft("部署", "使用 docker compose 启动全部服务。" * 6, "docs/deploy-a.md"),
        _draft("部署", "使用 docker compose 启动基础服务，其余按需。" * 6, "docs/deploy-b.md"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    # 无极性相反 → 不开冲突；但 host 模式下应入包
    assert not result.conflicts
    host = gate_drafts(drafts, [], Path("."), judge="host")
    assert any(c.conflict_type == "incoherent" for c in host.conflicts)
    assert not host.hold_sources  # host 不 hold peer 对


def test_parent_dir_h1_bucket_pairs():
    """标题不同但同父目录 + 正文首个 `# ` 一级标题相同 → 成对。"""
    drafts = [
        _draft("缓存策略", "# 会话缓存\n\n允许使用 redis 缓存会话。" + "允许使用 redis 缓存会话。" * 5,
               "docs/arch/a.md"),
        _draft("会话缓存", "# 会话缓存\n\n禁止使用 redis 缓存会话。" + "禁止使用 redis 缓存会话。" * 5,
               "docs/arch/b.md", signal="rules", ctype="prohibition"),
    ]
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert any(c.conflict_type == "incoherent" for c in result.conflicts)


def test_jaccard_outside_band_not_paired():
    """正文几乎相同（>=0.85，复制）或几乎无关（<0.35）都不成对。"""
    same = "同一段说明文字。" * 30
    drafts = [
        _draft("相同", same, "a/x.md"),
        _draft("相同", same, "b/y.md"),
        _draft("无关", "完全不同的主题，讲操作系统调度。" * 8, "a/z.md"),
    ]
    host = gate_drafts(drafts, [], Path("."), judge="host")
    assert not host.conflicts


def test_candidate_cap_counts_omitted(monkeypatch):
    """peer 候选超 10000 时按距 0.85 截断并计数（用小常量 monkeypatch 验证）。"""
    import rsi_boot.scanner.conflict_gate as gate_mod
    drafts = []
    for i in range(30):
        drafts.append(_draft("同题", f"允许使用 token{i} 作为约定。" * 4, f"ok{i}.md"))
        drafts.append(_draft("同题", f"禁止 token{i} 作为入参。" * 4, f"ban{i}.md",
                             signal="rules", ctype="prohibition"))
    monkeypatch.setattr(gate_mod, "_MAX_CANDIDATES", 10)
    result = gate_drafts(drafts, [], Path("."), judge="local")
    assert len(result.conflicts) <= 10
    assert result.omitted_candidates >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_conflict_gate.py -q`

Expected: FAIL（`judge` 参数不存在 → TypeError；旧极性用例已删）

- [ ] **Step 3: Write minimal implementation**

按 Interfaces 重写 `_detect_incoherent_conflicts` 并删除极性函数。`_local_peer_conflict` 的约束对象近似：

```python
def _strip_modals(text: str) -> str:
    blob = text
    for word in _MODAL_WORDS:
        blob = blob.replace(word, " ")
    return " ".join(sorted(_gate_words(blob)))

def _constraint_object(draft: DraftItem) -> str:
    h1 = _h1_text(draft.content)  # 正文首个 `# ` 行；无则 None
    return _strip_modals(h1 or draft.title)

def _local_peer_conflict(left, right):
    obj_l, obj_r = _constraint_object(left), _constraint_object(right)
    if not obj_l or obj_l != obj_r:
        return None
    pol_l = "prohibitive" if any(w in f"{left.title}{left.content}".lower() for w in _PROHIBITIVE) else "permissive"
    pol_r = "prohibitive" if any(w in f"{right.title}{right.content}".lower() for w in _PROHIBITIVE) else "permissive"
    if pol_l == pol_r:
        return None
    ...  # 组 ConflictDraft
```

注意：①「对象相同」用去语气词后的**排序词集合相等**判定；② 对象粒度是 H1/标题级（不含首句）——明文 vs 加密密码在同标题下对象相等，实际由候选带宽（标题+正文 Jaccard 须落 [0.35, 0.85)）挡住，这是刻意的：本地路径宁缺毋滥，细分语义判断归 host 模型；③ 词集用门内 CJK 感知 `_WORD_RE`（correlation `_words` 是 ASCII-only，会掏空纯中文对象）；④ 候选相似度 `_peer_sim` 计「标题 + 正文」（纯正文对 CJK 短句太苛刻，brief 自身用例验证）。极性判定用 `_PROHIBITIVE` 命中即 prohibitive，否则 permissive。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_conflict_gate.py -q`

Expected: PASS（含保留的 version/doc_code/progress 用例）

- [ ] **Step 5: Commit**

```text
refactor: 冲突门废除极性叉乘，改同桶整条比对 + host/local 双裁决
```

---

### Task 3: 日常提取适配新 peer 门（knowledge_extractor 不改逻辑，改测试）

**Files:**
- Modify: `tests/test_extract_incoherent.py`
- Modify: `src/rsi_boot/scanner/conflict_gate.py`（`_norm_title` 增加 `禁止：`/`禁止:` 前缀剥离——`extract_draft_rule` 给 rejected 抽取恒加 `禁止：` 前缀，不剥离则与历史同题条目永不共桶；对 version 家族标题比较无害）
- （`src/rsi_boot/learning/knowledge_extractor.py` 不改）

**Interfaces:**
- Consumes: 新 `gate_drafts([new_draft], [], Path("."), peers=peers)`（默认 local）。
- 关键语义变化：`auto-extract` 来源只进**标题桶**（`_peer_parent_key` 返回 None）。旧用例标题「禁止：pydantic」vs「API 用 pydantic」规范化后不同 → 不再成对。把测试数据改成同标题：

- [ ] **Step 1: Write the failing test（改写现有文件）**

- `_PROHIBIT_BODY` 保持不变；`_PERMIT_BODY` 改为 `"允许使用 pydantic 作为入参。 "`（统一标题「pydantic 入参」后，旧「校验请求体」正文 Jaccard 只有 0.333 出带；共享「作为入参」后 0.600 落带）。A/B 变体用 `_PERMIT_BODY_B`（首句改「，解析请求体。」），sim 0.500。
- 所有用例中历史条目标题与新提取标题统一为 `"pydantic 入参"`（新提取内容首行 `pydantic 入参\n禁止…`；extractor 会加 `禁止：` 前缀，靠 `_norm_title` 剥离后共桶）：
  - `test_gate_peers_incoherent_holds_draft_only`：draft 标题 `"pydantic 入参"`（prohibition），peer 标题 `"pydantic 入参"`（convention）。断言不变（hold 只含 `auto-extract`）。
  - `test_daily_new_prohibition_vs_active_permission_opens_incoherent`：历史 `KnowledgeItem(title="pydantic 入参", …)`；`_insert_rejected(db, f"pydantic 入参\n{_PROHIBIT_BODY}")`。断言不变。
  - `test_empty_source_url_peer_still_persists_incoherent`、`test_duplicate_peer_source_url_unique_historical_ids`、`test_keep_item_item_key_archives_only_that_history`、`test_keep_item_hashed_url_archives_only_hashed_peer`：同样把 `"API 用 pydantic"` 系列标题改成 `"pydantic 入参"`（A/B 变体用 `"pydantic 入参"` 同标题、正文微调保持 Jaccard 在带内）。
  - `test_identical_title_merges_without_conflict`：标题已相同（`禁止：pydantic`），但注意新门下同标题 + 正文 Jaccard ≥ 0.85（完全相同）不成对 → 仍无冲突，断言不变，应直接通过。
  - 新增一条：

```python
def test_gate_peers_different_object_not_paired():
    """日常提取 vs 历史：对象不同（明文 vs 加密密码）不开 incoherent。"""
    drafts = [
        DraftItem("密码存储", "禁止写入明文密码到数据库。 " * 6,
                  "prohibition", "auto-extract", ["signal:rules"], "rules"),
    ]
    peers = [
        DraftItem("密码存储", "允许写入加密密码到数据库。 " * 6,
                  "convention", "docs/crypto.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [], Path("."), peers=peers)
    assert not any(c.conflict_type == "incoherent" for c in result.conflicts)
    assert "auto-extract" not in result.hold_sources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_extract_incoherent.py -q`

Expected: 本任务在 Task 2 之后执行（见「执行顺序与依赖」），此处验证的是**新门下的红**：`test_gate_peers_different_object_not_paired` 应先红（若 Task 2 的 `_local_peer_conflict` 对象比较未覆盖 drafts×peers 分支）；其余改标题用例在 Task 2 落地后应直接转绿。若意外全绿，说明新门已满足语义，直接进 Step 4。

- [ ] **Step 3: Write minimal implementation**

预期零生产代码改动。若 `test_gate_peers_incoherent_holds_draft_only` 断言 `hold_sources == ["auto-extract"]` 失败（因为 local 裁决对 drafts×peers 应只 hold draft 侧），确认 Task 2 的 drafts×peers 分支 `hold_right=False` 语义已保留；否则在 `_detect_incoherent_conflicts` 里区分：同侧对（draft×draft）hold 两侧，跨侧对（draft×peer）只 hold draft。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_extract_incoherent.py tests/test_conflict_gate.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```text
test: 日常提取冲突用例迁到标题桶配对
```

---

### Task 4: `host_judge_queue.json` 写入 / 删除

**Files:**
- Modify: `src/rsi_boot/cli/bootstrap_command.py`
- Test: `tests/test_bootstrap_judge_flags.py`（追加用例）

**Interfaces:**
- Produces（bootstrap_command 内部辅助函数）：

```python
_QUEUE_NAME = "host_judge_queue.json"
_QUEUE_CONTENT_CAP = 6000  # 与 slice max_tokens 1500 × 4 字符一致

def _truncate_for_queue(text: str) -> tuple[str, bool]:
    """超 _QUEUE_CONTENT_CAP 截断并标 truncated。"""

def _conflict_sides(
    gate: GateResult, drafts_by_source: Dict[str, DraftItem],
) -> Dict[str, DraftItem]:
    """left/right source → DraftItem（库内同伴已在 drafts 里，直接索引）。"""

async def _write_host_judge_queue(
    runtime: Any, rsi_dir: Path, project_id: str, run_id: str,
    gate: GateResult, drafts: List[DraftItem],
) -> tuple[Path, int]:
    """持久化冲突后，把本 run 全部 open 冲突（含 conflict_id）写成队列文件。
    返回 (队列路径, 未决组数)。写失败抛 OSError（调用方转 exit 1）。"""

def _delete_host_judge_queue(rsi_dir: Path) -> None:
    """--local-judge：存在则删，避免 agent 误读旧 run。"""
```

- 队列结构（与 spec §5.1 一致）：`{bootstrap_run_id, generated_at, judge: "host", capped: bool, omitted: int, items: [{conflict_id, conflict_type, left: {title, content, source}, right: {...}, recommended, recommended_reason, truncated}]}`。
- `conflict_id` 来源：`_write_host_judge_queue` 在 `persist_knowledge_conflicts` 之后调用 `runtime.conflict_detector.list_conflicts(project_id, "open", bootstrap_run_id=run_id)`（Task 5 的分页/run 过滤若未落地，先落地 Task 5 再做本任务——**任务顺序：Task 5 先行**），按 `(item_id → source, user_rule_path)` 与 `gate.conflicts` 对齐取 id；title/content 从 `drafts_by_source` 取，缺失侧（如 doc_code 的代码骨架侧 `signal:code`）用 `{"title": right_source, "content": "", "source": right_source}` 兜底。
- `capped` = `gate.omitted_candidates > 0`，`omitted` = `gate.omitted_candidates`。
- `--host-judge` 且无冲突：写 `items: []` 的空队列（`capped: false, omitted: 0`）。
- 调用点（`run_bootstrap` 收尾段，`persist_knowledge_conflicts` 之后）：`judge == "host"` → 写队列，失败则 `print(..., file=sys.stderr); return 1`；`judge == "local"` → `_delete_host_judge_queue(rsi_dir)`。
- 报告字段（Task 7 做终端/Markdown 渲染，本任务先在 `BootstrapReport` dataclass **声明字段并赋值**——`write_json` 走 `asdict`，不声明则 JSON 里 KeyError）：`judge: str = ""`、`judge_candidates: int = 0`、`judge_unresolved: int = 0`（host 时为队列 items 数）、`judge_queue_path: str = ""`（host 时为 str 路径）、`judge_omitted: int = 0`（= `gate.omitted_candidates`）。
- 对齐取 `conflict_id` 的注意点：DB 行 `user_rule_path` 是 `_norm_src(right)` 归一化值，`item_id` 是 left 侧条目 id；用现成 `_source_to_item_id(runtime, project_id)`（795 行）求逆映射得 `item_id → source`，两侧都经 `_norm_src` 后以 `(left, right)` 键与 `gate.conflicts` 对齐。

- [ ] **Step 1: Write the failing test**

```python
async def test_host_judge_writes_queue_with_full_content(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "a.md").write_text(
        "# 部署\n\n" + "允许使用 docker compose 启动全部服务。" * 8, encoding="utf-8")
    (root / "docs" / "b.md").write_text(
        "# 部署\n\n" + "禁止使用 docker compose 启动全部服务，只起基础镜像。" * 8,
        encoding="utf-8")
    assert await run_bootstrap(_args(root, host_judge=True)) == 0
    queue = root / ".rsi" / "host_judge_queue.json"
    assert queue.is_file()
    data = json.loads(queue.read_text(encoding="utf-8"))
    assert data["judge"] == "host"
    assert data["bootstrap_run_id"]
    assert data["items"], "同标题近似对必须入包"
    item = data["items"][0]
    assert item["conflict_id"]
    assert item["left"]["content"] and item["right"]["source"]
    assert item["recommended"] in ("keep_item", "keep_peer", "coexist")
    # host 不裁决 peer：两侧都不应因 peer 被 hold 成 pending_review
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "host"
    assert report["judge_queue_path"].endswith("host_judge_queue.json")


async def test_local_judge_deletes_stale_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    rsi = root / ".rsi"
    rsi.mkdir()
    stale = rsi / "host_judge_queue.json"
    stale.write_text('{"items": []}', encoding="utf-8")
    assert await run_bootstrap(_args(root, local_judge=True)) == 0
    assert not stale.exists()
    report = json.loads((rsi / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "local"
```

（文件顶部补 `import json`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_bootstrap_judge_flags.py -q`

Expected: FAIL（队列文件不存在 / `report["judge"]` KeyError）

- [ ] **Step 3: Write minimal implementation**

按 Interfaces 实现。`run_bootstrap` 里 `judge = "host" if args.host_judge else "local"`（校验已保证恰一个），传 `gate_drafts(..., judge=judge)`；`_detect_incoherent_conflicts` 的 retarget 文案从「冲突检测（极性配对）」改为「冲突检测（同桶配对）」。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_bootstrap_judge_flags.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```text
feat: --host-judge 写冲突工作包队列，--local-judge 清旧队列
```

---

### Task 5: `rsi_conflicts` list 分页 + `bootstrap_run_id` 过滤

**Files:**
- Modify: `src/rsi_boot/injector/conflict.py`（`list_conflicts`，324 行）
- Modify: `src/rsi_boot/api/tools/conflicts_tool.py`
- Test: `tests/test_conflicts_tool_batch.py`（新建）

**Interfaces:**
- Produces:

```python
async def list_conflicts(
    self, project_id: str, status: str = "open",
    *, bootstrap_run_id: Optional[str] = None,
    limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]: ...
```

- run 过滤口径（与 `knowledge_accept._conflicts_for_run` 相同）：`marker = f"bootstrap_run_id:{run_id}"`；冲突的 `item_id` 侧 tags 含 marker，或 `user_rule_path` 侧（先 `_peer_row` 同款解析：`item:<id>` / `url#id` / 裸 url）对应条目 tags 含 marker。SQL 侧先按 status 取 open 全量（去掉硬编码 `LIMIT 50`，改 Python 侧过滤后分页），再按 marker 过滤，最后 `offset/limit` 切片。`limit` 夹到 `[1, 200]`。
- 工具 schema 增加：`bootstrap_run_id`（string）、`limit`（integer，默认 100，最大 200）、`offset`（integer，默认 0）。handle 的 list 分支透传。
- 注意 `knowledge_accept._conflicts_for_run` 调用处签名兼容（它用位置参数 `list_conflicts(project_id, "open")`，新增参数全为关键字默认，零改动）。

- [ ] **Step 1: Write the failing test**

```python
"""rsi_conflicts list 分页 / run 过滤 + resolve 批量 decisions。"""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.injector.conflict import ConflictDetector


async def _item(db, project_id, title, source_url, tags, status="pending_review"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain,"
        " tags, source_url, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'convention', NULL, ?, ?, ?, ?, ?)",
        (item_id, project_id, title, f"{title} 正文 " * 6,
         json.dumps(tags, ensure_ascii=False), source_url, status, now, now),
    )
    await conn.commit()
    return item_id


async def _conflict(db, project_id, item_id, peer_source):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    cid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO rule_conflicts (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
        " user_rule_hash, conflict_type, status, resolution_note, detected_at)"
        " VALUES (?, ?, ?, ?, 'excerpt', ?, 'incoherent', 'open', 'recommended:coexist', ?)",
        (cid, project_id, item_id, peer_source, uuid.uuid4().hex, now),
    )
    await conn.commit()
    return cid


def _runtime(db):
    return SimpleNamespace(conflict_detector=ConflictDetector(db), project_id="p1")


async def test_list_filters_by_bootstrap_run_id(db):
    run_a, run_b = uuid.uuid4().hex, uuid.uuid4().hex
    item_a = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    item_b = await _item(db, "p1", "乙", "docs/b.md", ["signal:docs", f"bootstrap_run_id:{run_b}"])
    peer_a = await _item(db, "p1", "甲伴", "docs/a2.md", ["signal:docs", f"bootstrap_run_id:{run_a}"])
    await _conflict(db, "p1", item_a, "docs/a2.md")   # 双侧 run A
    await _conflict(db, "p1", item_b, "docs/a2.md")   # item 侧 run B，peer 侧 run A → 算 run A
    await _conflict(db, "p1", item_b, "docs/nowhere.md")  # 只 run B
    rt = _runtime(db)
    listed = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_a,
    })
    assert listed["status"] == "success"
    assert listed["data"]["count"] == 2
    listed_b = await conflicts_tool.handle(rt, {
        "action": "list", "bootstrap_run_id": run_b,
    })
    assert listed_b["data"]["count"] == 1


async def test_list_paginates_with_limit_offset(db):
    item = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs"])
    for i in range(5):
        await _conflict(db, "p1", item, f"docs/peer{i}.md")
    rt = _runtime(db)
    page1 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 0})
    page2 = await conflicts_tool.handle(rt, {"action": "list", "limit": 2, "offset": 2})
    assert page1["data"]["count"] == 2 and page2["data"]["count"] == 2
    ids1 = {c["id"] for c in page1["data"]["conflicts"]}
    ids2 = {c["id"] for c in page2["data"]["conflicts"]}
    assert not ids1 & ids2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_conflicts_tool_batch.py -q`

Expected: FAIL（`bootstrap_run_id`/`limit`/`offset` 未透传 → count 为 3 / 分页不生效）

- [ ] **Step 3: Write minimal implementation**

`list_conflicts` 重写：status 过滤后取全量（`ORDER BY c.detected_at DESC`，去掉 LIMIT 50）；`bootstrap_run_id` 非空时逐行查两侧 tags（复用 `_peer_row` 解析对侧）；最后 `rows[offset:offset + limit]`。工具 handle：

```python
if action == "list":
    items = await detector.list_conflicts(
        project_id, status=str(arguments.get("status") or "open"),
        bootstrap_run_id=arguments.get("bootstrap_run_id") or None,
        limit=int(arguments.get("limit") or 100),
        offset=int(arguments.get("offset") or 0),
    )
    return {"status": "success", "data": {"conflicts": items, "count": len(items)}}
```

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_conflicts_tool_batch.py tests/test_conflict.py tests/test_version_conflict.py tests/test_conflict_explain.py -q`

Expected: PASS（旧 list 调用方零改动）

- [ ] **Step 5: Commit**

```text
feat: rsi_conflicts list 支持 bootstrap_run_id 过滤与分页
```

---

### Task 6: `rsi_conflicts` resolve 批量 `decisions`

**Files:**
- Modify: `src/rsi_boot/injector/conflict.py`（新增 `resolve_batch`）
- Modify: `src/rsi_boot/api/tools/conflicts_tool.py`
- Test: `tests/test_conflicts_tool_batch.py`（追加）

**Interfaces:**
- Produces:

```python
async def resolve_batch(
    self, decisions: List[Dict[str, str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """逐条调 self.resolve；返回 {"resolved": [...], "failed": [{conflict_id, reason}]}。
    部分失败不回滚已成功。"""
```

- 工具 schema `resolve` 增加 `decisions`：`{"type": "array", "items": {"type": "object", "properties": {"conflict_id": ..., "resolution": ..., "note": ...}, "required": ["conflict_id", "resolution"]}, "maxItems": 200}`。handle 分支：`decisions` 存在时——`len > 200` → `{"status": "error", "message": "单批最多 200 条"}`（一条不执行）；否则 `resolve_batch` 并返回 `{"status": "success", "data": {"resolved": [...], "failed": [...]}}`；每条成功且 `runtime.decisions` 存在时 `decisions.close(conflict_id)`。`decisions` 存在时忽略顶层 `conflict_id`/`resolution`。

- [ ] **Step 1: Write the failing test**

```python
async def test_resolve_decisions_batch_partial_failure_no_rollback(db):
    item = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs"])
    ok1 = await _conflict(db, "p1", item, "docs/p1.md")
    ok2 = await _conflict(db, "p1", item, "docs/p2.md")
    rt = _runtime(db)
    result = await conflicts_tool.handle(rt, {
        "action": "resolve",
        "decisions": [
            {"conflict_id": ok1, "resolution": "coexist"},
            {"conflict_id": "不存在的id", "resolution": "coexist"},
            {"conflict_id": ok2, "resolution": "keep_item"},
        ],
    })
    assert result["status"] == "success"
    data = result["data"]
    assert {r["conflict_id"] for r in data["resolved"]} == {ok1, ok2}
    assert [f["conflict_id"] for f in data["failed"]] == ["不存在的id"]
    # 已成功的不回滚
    conn = await db.connect()
    async with conn.execute(
        "SELECT status FROM rule_conflicts WHERE id = ?", (ok1,)
    ) as cur:
        assert (await cur.fetchone())["status"] == "coexist"


async def test_resolve_decisions_over_200_rejected_without_executing(db):
    item = await _item(db, "p1", "甲", "docs/a.md", ["signal:docs"])
    cid = await _conflict(db, "p1", item, "docs/p1.md")
    rt = _runtime(db)
    result = await conflicts_tool.handle(rt, {
        "action": "resolve",
        "decisions": [{"conflict_id": cid, "resolution": "coexist"}] * 201,
    })
    assert result["status"] == "error"
    assert "200" in result["message"]
    conn = await db.connect()
    async with conn.execute(
        "SELECT status FROM rule_conflicts WHERE id = ?", (cid,)
    ) as cur:
        assert (await cur.fetchone())["status"] == "open"  # 一条都没执行
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_conflicts_tool_batch.py -q`

Expected: FAIL（`decisions` 被忽略 → 走单条分支报「冲突不存在/已关闭，或 resolution 非法」）

- [ ] **Step 3: Write minimal implementation**

`resolve_batch` 循环 `await self.resolve(d["conflict_id"], d["resolution"], d.get("note") or "")`；返回 None 进 `failed`（reason `"冲突不存在/已关闭，或 resolution 非法"`），否则进 `resolved`。工具层按 Interfaces 分发。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_conflicts_tool_batch.py tests/test_conflict.py tests/test_recall_decisions.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```text
feat: rsi_conflicts resolve 支持最多 200 条批量 decisions
```

---

### Task 7: 报告 judge 字段与终端/ Markdown 渲染

**Files:**
- Modify: `src/rsi_boot/scanner/report.py`
- Test: `tests/test_bootstrap_report_md.py`（追加）

**Interfaces:**
- `BootstrapReport` 新增字段：

```python
judge: str = ""                    # "host" | "local" | ""（dry-run）
judge_candidates: int = 0          # 候选组数（gate.conflicts 总数）
judge_unresolved: int = 0          # host：队列 items 数；local：0
judge_queue_path: str = ""         # host：.rsi/host_judge_queue.json
judge_omitted: int = 0             # 超 10000 被截断的候选数
```

- `render_terminal` 在非 dry-run 分支、冲突计数行之前加：
  - `判断: judge=host，候选 N 组，未决 M 组，工作包 <path>`（host）
  - `判断: judge=local，候选 N 组`（local）
  - `judge_omitted > 0` 追加 `（超上限截断 X 组未入包，建议收窄 --include 或分目录再学）`。
- `write_markdown` 在「## 冲突组」段末尾加同样一行（host 时含队列路径）。

- [ ] **Step 1: Write the failing test**

```python
def test_render_terminal_shows_judge_line(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        judge="host", judge_candidates=12, judge_unresolved=12,
        judge_queue_path=str(tmp_path / ".rsi" / "host_judge_queue.json"),
    )
    text = report.render_terminal()
    assert "judge=host" in text
    assert "12" in text
    assert "host_judge_queue.json" in text


def test_render_terminal_shows_omitted_hint(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path), judge="local", judge_candidates=10000,
        judge_omitted=320,
    )
    text = report.render_terminal()
    assert "judge=local" in text
    assert "320" in text
    assert "--include" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_bootstrap_report_md.py -q`

Expected: FAIL（字段不存在 → TypeError）

- [ ] **Step 3: Write minimal implementation**

加字段与渲染行。`bootstrap_command.py` 在 Task 4 已赋值；本任务确认 `report.judge_unresolved` 在 local 路径保持 0。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_bootstrap_report_md.py tests/test_bootstrap_judge_flags.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```text
feat: 学习报告输出判断模式与宿主工作包路径
```

---

### Task 8: 注入文案 `_DECISIONS_BODY` 扩写

**Files:**
- Modify: `src/rsi_boot/injector/targets.py`
- Test: `tests/test_recall_decisions.py`（追加断言）

**Interfaces:**
- 约束：`rsi-decisions.mdc` body ≤ `MAX_DECISIONS_CHARS`（800）字符（现有断言 `len(body) <= 800` 不变）。
- 新 `_DECISIONS_BODY` 草案（约 300 字符，写文件前再数字数）：

```python
_DECISIONS_BODY = (
    "有 decisions 时由你调用 rsi_conflicts / rsi_knowledge_review 落库。"
    "用户要自动执行（你看着办/按推荐）时立刻用 recommended 调工具。"
    "用户要展开或问影响面时用 explain 或卡上的 sides/impact，不要关闭这张卡。"
    "不要让用户自己去终端跑 rsi。"
    "用户说重新学习时你执行 rsi bootstrap --consent --host-judge（要清库先 rsi wipe --yes），"
    "看到「必须指定 --host-judge 或 --local-judge」就加 --host-judge 重跑，不要改用 --local-judge。"
    "学完读 .rsi/host_judge_queue.json 或 rsi_conflicts list（带 bootstrap_run_id），"
    "每批最多 200 条 resolve，直到未决为 0；只有你不确定的才留给以后的 recall 卡。"
)
```

- [ ] **Step 1: Write the failing test**

`tests/test_recall_decisions.py` 追加：

```python
def test_decisions_mdc_covers_host_judge_recipe(tmp_path):
    target = CursorRuleTarget(tmp_path)
    target.write(MemoryBundle())
    text = (tmp_path / ".cursor" / "rules" / "rsi-decisions.mdc").read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    assert len(body) <= 800
    assert "--host-judge" in text
    assert "rsi wipe --yes" in text
    assert "host_judge_queue.json" in text
    assert "200" in text
    assert "不要让用户自己去终端" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 -m pytest tests/test_recall_decisions.py -q`

Expected: FAIL（`--host-judge` 不在文案里）

- [ ] **Step 3: Write minimal implementation**

替换 `_DECISIONS_BODY`。若超 800 字符，先砍「看到…重跑」句的修饰词，不得删「不要让用户自己去终端跑 rsi」与「200」。

- [ ] **Step 4: Run tests**

Run: `python -X utf8 -m pytest tests/test_recall_decisions.py tests/test_injector.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```text
feat: 常驻抉择纪律覆盖 --host-judge 重新学习与队列批处理
```

---

### Task 9: 存量 `_args` 与回归测试修复

**Files:**
- Modify: `tests/test_bootstrap.py`（`_args`，42 行）
- Modify: `tests/test_bootstrap_report_md.py`（`_args`，55 行）
- Modify: `tests/test_bootstrap_progress.py`（`_args`，122 行）
- Modify: `tests/test_bootstrap_lanes.py`（`_args`，16 行）
- Modify: `tests/test_bootstrap_apply_e2e.py`（`_args`，19 行）
- Modify: `tests/test_knowledge_freshness.py`（`_args`，22 行）
- Modify: `tests/test_review_queue.py`（`_args`，39 行）
- Modify: `tests/test_rule_seeds.py`（`_args`，97 行）

**Interfaces:**
- 每个 `_args` 的 `defaults` 增加 `include=[], host_judge=False, local_judge=True`（存量用例全部走本地裁决，语义最接近旧行为；`include` 顺带补齐，避免依赖 `getattr` 默认值的两套口径）。
- `tests/test_bootstrap.py::test_bootstrap_end_to_end` 的 dry-run 调用（`_args(root, dry_run=True)`）会被 defaults 带上 `local_judge=True`——合法（dry-run 豁免但给了旗标也不报错），无需特判。
- 逐文件跑红绿，不改任何断言；若某用例因新门多出 incoherent 而失败（如 lanes/e2e 里同标题文档），优先检查测试数据是否撞了标题桶——撞了就把测试文档标题改开，**不要**放宽产品断言。

- [ ] **Step 1: Write the failing test**

本任务即修复：先全量跑一遍看红：

Run: `python -X utf8 -m pytest tests/ -q -x --ignore=tests/test_conflict_gate.py --ignore=tests/test_extract_incoherent.py`

Expected: FAIL（`_args` 缺 `host_judge`/`local_judge` → `run_bootstrap` 返回 2）

- [ ] **Step 2: 改 8 个 `_args`**

按 Interfaces 加 defaults。

- [ ] **Step 3: Run tests**

Run: `python -X utf8 -m pytest tests/ -q`

Expected: PASS（全量，含 Task 2/3 改过的冲突门与提取用例）

- [ ] **Step 4: Commit**

```text
test: 存量 bootstrap 用例默认走 --local-judge
```

---

### Task 10: README 更新

**Files:**
- Modify: `README.md`（快速上手第 4 步与「重新学习」段）

**Interfaces:**
- `rsi bootstrap` 示例改 `rsi bootstrap --local-judge`；`--force` 行同样补 `--local-judge`。
- 加一句：「在 Cursor 对话里由 agent 重新学习时走 `--host-judge`（冲突工作包由宿主模型当场裁决）；你自己敲终端用 `--local-judge`（本地整条比对，宁缺毋滥）。两者必须且只能给一个，`--dry-run` 除外。」
- 「重新学习」段的 `rsi bootstrap` 改 `rsi bootstrap --local-judge`。

- [ ] **Step 1: 改 README**（无测试；文档改动）

- [ ] **Step 2: 全量回归 + 自查**

Run: `python -X utf8 -m pytest tests/ -q`

Expected: PASS。然后 `git status` 确认改动文件清单与本计划 File map 一致。

- [ ] **Step 3: Commit**

```text
docs: bootstrap 判断旗标写进快速上手
```

---

## 执行顺序与依赖

```
Task 1（旗标校验） ──► Task 2（冲突门） ──► Task 3（提取适配）
                     │
                     ▼
              Task 5（list 过滤/分页） ──► Task 4（队列文件，依赖 list 的 run 过滤）
                     │
                     ▼
              Task 6（批量 resolve）
                     │
                     ▼
              Task 7（报告） ──► Task 8（注入文案） ──► Task 9（回归修复） ──► Task 10（README）
```

Task 4 依赖 Task 5 的 `bootstrap_run_id` 过滤（队列要带 conflict_id）；Task 3 依赖 Task 2 的新门（用例按新语义写）。Task 9 若提前做会让中间态全量测试变红，放在最后。

## Self-review

- **Spec 覆盖**：§4.1 分桶/硬顶 → Task 2；§4.2 本地裁决 → Task 2；§4.3 队列与当场循环（rsi 侧）→ Task 4/5/6；§4.4 旗标与失败 → Task 1/4；§4.5 注入 → Task 8；§4.6 队列写失败 exit 1 → Task 4；§5.1 队列结构 → Task 4；§6.1 CLI → Task 1；§6.2 MCP → Task 5/6；§7 测试要点 → 各 Task 用例逐条对应（裸跑 exit 2 / 双旗标 exit 2 / 明文 vs 加密 / 同对象一禁一许 / 极性共享词不再成对 / host 不改 status 且 version/doc_code 在队列 / list 按 run / 201 拒绝与 200 部分失败 / local 删队列）。
- **占位符扫描**：无 TBD；所有测试代码完整可粘贴。
- **类型一致性**：`gate_drafts` 新参数 `judge: str = "local"` 与 extractor 零改动兼容；`list_conflicts` 新参数全关键字默认，`knowledge_accept` 位置调用兼容；`GateResult.omitted_candidates` 有默认值，旧构造兼容。
- **风险**：① `_strip_modals` 的「排序词集合相等」对长文档偏严——这是刻意的（宁缺毋滥），host 路径兜底；② 队列 conflict_id 对齐依赖 `(item_id→source, user_rule_path)` 双键，doc_code 右侧是 `signal:code` 骨架源，drafts 索引可能缺 → 已规定兜底 `{title: source, content: ""}`。
