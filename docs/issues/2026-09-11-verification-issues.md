# RSI Boot v3.0 实仓验证 Issue 清单

来源：2026-09-11 对真实仓库 `aigate-dgp`（Java 多模块 Maven 项目，24 万+ 行级代码信号）的全链路验证。
验证范围：init → bootstrap（dry-run/正式）→ 审批 → recall → 注入 → 冲突检测 → 裁决 → 反馈 → stats。
结论：主链路全部打通；以下 6 个问题按优先级排列。

| # | 优先级 | 类型 | 标题 | 状态 |
|---|--------|------|------|------|
| 1 | P1 | 健壮性 bug | SQLITE_BUSY 重试机制被普遍绕过 | fixed (2026-09-11) |
| 2 | P1 | 可用性缺陷 | bootstrap 审批队列洪水（单次 2308 条待审） | fixed (2026-09-11) |
| 3 | P2 | 质量 bug | 画像 expertise/framework 提取错误 | fixed (2026-09-11) |
| 4 | P2 | 设计局限 | 冲突检测子串匹配与极性窗口误报 | fixed (2026-09-11) |
| 5 | P3 | UX | Windows 下 CLI 日志走 stderr 显示为红色错误 | fixed (2026-09-11) |
| 6 | P3 | 设计观察 | 禁止项冷启动为空 | fixed (2026-09-11) |
| 7 | P0 | 隔离 bug | 多项目 MCP 共用 `~/.rsi/rsi.db` + 缺省 `project_id=default` 串记忆 | fixed (2026-09-11) |

---

## ISSUE-1 [P1] SQLITE_BUSY 重试机制被普遍绕过

**现象**：验证中真实出现 `sqlite3.OperationalError: database is locked`，抛点在 `injector/conflict.py:171`（scan 的 INSERT）。

**根因**：`SQLiteClient.commit()` 实现了 §5.3 的 SQLITE_BUSY 指数退避重试（base 0.1s、full jitter、最多 3 次），但全代码库仅 `services/log_service.py` 走该包装；其余 **16 个文件共 34 处**直接 `conn.commit()` 裸提交，全部绕过重试：

| 文件 | 裸提交次数 |
|------|-----------|
| learning/proposal_engine.py | 7 |
| services/knowledge_service.py | 4 |
| learning/knowledge_extractor.py、data/vec.py、learning/snapshot_store.py | 各 3 |
| injector/conflict.py、feedback/implicit_tracker.py、strategy/recall.py、scheduler/tasks.py | 各 2 |
| injector/rule_injector.py、services/profile_service.py、strategy/engine.py、scanner/profile_generator.py、scanner/incremental.py、data/migrate.py | 各 1 |

多连接并发（如 MCP server 运行中 + CLI/嵌入方同步写库）时，任一裸提交点都可能直接抛锁错误。

**建议**：将重试逻辑下沉为统一入口——`SQLiteClient` 提供 `execute/commit` 强制收口（或封装 `atomic()` 上下文管理器），禁止业务层持有裸连接提交；对 `execute` 侧的 BUSY 同样补重试（当前仅 commit 有）。

**验收**：并发写压测（2 进程同时批量写）无 `database is locked` 抛出；grep 无 `conn.commit()` 裸调用残留（`data/sqlite.py` 自身除外）。

**修复记录（2026-09-11）**：重试下沉为连接级代理——`SQLiteClient.connect()` 返回 `RetryingConnection`，`execute`（含 `await` 与 `async with` 两种形态）与 `commit` 统一走 `_with_busy_retry`（base 0.1s、full jitter、最多 3 次，仅锁错误重试）；业务层 34 处裸调用零改动自动获得重试。新增 `busy_timeout_ms=5000` 连接参数。测试：`tests/test_sqlite_retry.py` 7 例（含真实库瞬态锁集成）；`test_degradation.py` 两例改 patch 底层裸连接。

---

## ISSUE-2 [P1] bootstrap 审批队列洪水

**现象**：对 aigate-dgp 执行一次 bootstrap 产出 **2308 条** `pending_review` 草稿（documentation 2094 / architecture 213 / convention 1）。用户不可能逐条审批，审批队列实质不可用——而审批闸门又是记忆生效的唯一入口，形成死结。

**建议**（可组合）：
1. bootstrap 产出按信号强度（文档引用度、代码骨架规模、git 关联数）排序，**默认仅 Top N（如 50）入审批队列**，其余落 `archived` 状态备查；
2. `rsi_knowledge_review` 增加批量操作（按 id 列表 / 按 content_type / 全部）；
3. 高置信类别（如代码骨架摘要）可配置免审直通（`bootstrap.auto_approve` 开关，默认关）。

**验收**：对同一仓库 bootstrap 后 pending_review 数量 ≤ 配置上限；批量审批 100 条一次调用完成。

**修复记录（2026-09-11）**：采纳建议 1+2——新增配置 `bootstrap.review_queue_cap`（默认 50），bootstrap 收尾按优先级（convention > architecture > faq > documentation，同类按写入先后）保留前 N 条 pending_review，溢出置 `archived`（报告新增 `review_queue_archived` 计数）；`archived` 参与去重，force 重跑不重新入队。`rsi_knowledge_review` 支持 `ids` 数组 / `all_pending`（可配 `content_type` 过滤、`include_archived`），`KnowledgeService.review_batch` 单次提交 + 单次注入重写；单个 `review` 放行 `archived → active`。建议 3（auto_approve 直通）未实施——审批闸门是记忆质量底线，暂不开口子。测试：`tests/test_review_queue.py` 11 例。

---

## ISSUE-3 [P2] 画像 expertise / framework 提取错误

**现象**：aigate-dgp 画像产出 `expertise = "com,org,lombok,java,io"`（Java 包名前缀，非专长主题）；`framework` 与 `test_framework` 均为空——但项目根 `pom.xml` 明确是 Spring Boot 多模块工程，且存在大量 `*Test.java`。

**根因推测**：expertise 提取规则误将 import 包名片段当主题词；framework 探测未覆盖 Maven `pom.xml` 依赖解析。

**建议**：expertise 提取加停用词表（`com/org/io/net` 等包名前缀、单字母、纯通用词）；framework 探测补 Maven/Gradle 依赖签名（spring-boot-starter → spring_boot，junit → junit）。

**验收**：对 aigate-dgp 重跑 bootstrap，画像 `framework` 含 Spring 系、`test_framework` 含 JUnit，expertise 不出现包名前缀。

**修复记录（2026-09-11）**：三处联动——① `code_scanner` Java import 正则改取前两段并归一化（`_java_import_root`：`com.baomidou` → `baomidou`，`org.springframework` → `springframework`，`java/javax/jdk/sun` 整包跳过，支持 static import）；② `config_scanner` 新增 `_parse_pom`（artifactId 正则 + `_POM_FRAMEWORK_HINTS` 子串匹配：spring-boot-starter-* → spring、mybatis、quarkus 等；junit/testng → test_framework；build_system=maven、language=java）；③ `profile_generator` 停用词表补 JVM/vendor 前缀兜底。测试：`tests/test_java_profile.py` 5 例。

---

## ISSUE-4 [P2] 冲突检测子串匹配与极性窗口误报

**现象**（验证实录）：
1. key phrase `Executors` 命中用户规则中 `ThreadPoolExecutor` 的内部子串（无词边界）；
2. 学习记忆「禁止：ResponseEntity」对用户规则「统一 ApiDataResponse<T> 而非 ResponseEntity」报 `contradiction`——两者方向其实一致，但命中窗口（±50 字符）内无禁止词即判矛盾；
3. convention「ApiDataResponse」在含「禁止」词汇的窗口中命中，跳过 overlap 后落入 stale 判定，语义勉强。

**定位说明**：§4.9 设计为「疑似」级 + 用户裁决兜底，误报不阻断注入，风险可控；但误报率直接影响用户对该功能的信任。

**建议**：key phrase 匹配加词边界（英文按 `\b`，中文维持子串）；contradiction 判定改为「窗口内存在许可词」才报（当前是「无禁止词即报」，过宽）；overlap 与 stale 互斥优先级固化（overlap 优先）。

**验收**：构造方向一致的用户规则用例不再报 contradiction；`ThreadPoolExecutor` 不命中 `Executors`。

**修复记录（2026-09-11）**：三条建议全部落地——① 新增 `_find_phrase`：ASCII 短语按词边界匹配（`\b` 仅施加于词字符端，`Executors.*` 类含通配短语不误伤），CJK 维持子串；② prohibition 矛盾判定改为「窗口内存在许可词」才报（中性窗口如「统一 X 而非 Y」方向一致，不报）；③ convention/experience 命中即 overlap，overlap 优先于 stale 互斥固化。测试：`test_conflict.py` 新增 4 例（含词边界正向命中对照），原 13 例全绿。

---

## ISSUE-5 [P3] Windows 下 CLI 日志走 stderr 显示为红色错误

**现象**：`rsi init` 等命令的 INFO 日志（如迁移进度）输出到 stderr，PowerShell 将其渲染为红色 `NativeCommandError` 块，观感如同命令失败。

**建议**：CLI 入口（`__main__.py`）默认日志级别 WARNING 且走 stdout；`--verbose` 时才恢复 INFO + stderr。MCP serve 模式维持现状（stdio 协议要求 stdout 纯净，日志必须 stderr）。

**验收**：Windows PowerShell 下 `rsi init` / `rsi recall` 无红色错误块；`rsi serve` 日志仍走 stderr。

**修复记录（2026-09-11）**：`setup_logging` 新增 `stream` 参数；新增 `setup_cli_logging(verbose)`——CLI 子命令（init/bootstrap/recall/knowledge）默认 WARNING + stdout，`--verbose` 恢复 INFO + stderr；serve 维持 INFO + stderr（stdio 协议要求 stdout 纯净）。全部子命令（含 knowledge 二级子命令）接受 `--verbose`。测试：`tests/test_cli_logging.py` 5 例（含 init 端到端 stderr 零输出断言）。

---

## ISSUE-6 [P3] 禁止项冷启动为空

**现象**：bootstrap 产物的 content_type 分布为 documentation 2094 / architecture 213 / convention 1 / **prohibition 0**。禁止项完全依赖使用中的 `rejected + comment` 反馈积累，新项目第一天没有禁止项可召回、可注入。

**观察**：目标仓库的 3 个手写规则文件（java-review-checklist 等）中含大量「禁止」句式（禁止直调 Mapper、禁止 Executors.*、禁止 @Autowired 字段注入……）——是现成的禁止项种子来源。

**建议**（可选增强）：bootstrap 扫描用户已有规则文件（`.cursor/rules/*.mdc`、`AGENTS.md`），用规则式提取（「禁止/不要/严禁」句式）产出 prohibition 草稿入审批队列。与 §4.9 冲突检测天然互补（种子来自用户规则，不会与其冲突）。

**验收**：对含手写禁止规则的仓库 bootstrap 后，审批队列出现对应 prohibition 草稿。

**修复记录（2026-09-11）**：新增 `scanner/rule_seed_scanner.py`——扫描 `.cursor/rules/*.mdc`（排除 rsi- 自产）、`.cursorrules`、`AGENTS.md`（托管块剔除），按禁止句式（禁止/严禁/不要/不得/避免/never/don't/must not/avoid 等）逐行提取，产出 `pending_review` prohibition 草稿（tags `signal:rules`，source_url 定位到行号，脱敏后写入）；随 config 维度门控，内容指纹去重保证幂等。审批限量优先级中 prohibition 提至第二位（仅次 convention）——禁止项召回置顶/常驻注入，冷启动价值最高。报告新增 `prohibition_seeds` 计数。测试：`tests/test_rule_seeds.py` 6 例（含端到端与幂等）。

---

## 附：验证环境记录

- 目标仓库：`D:\project\study\aigate-dgp`（验证后产物已全部清理，仓库恢复原样）
- RSI_HOME：临时目录隔离（已删除）；验证脚本（已删除）
- 另有一条非产品问题的教训：同一进程内混用同步 `sqlite3` 连接与运行时 aiosqlite 连接会导致锁库挂起。产品单连接设计本身无问题，建议在未来「嵌入用法」文档中显式警示。

---

## 附 2：第二轮前端仓库复验（2026-09-11，frontend_dbaudit_aigate）

目标仓库：`D:\project\study\web\frontend_dbaudit_aigate`（Vue3 + TS + Vite，约 975 个源文件，其中 `.vue` 243 个）。验证后产物已全部清理，仓库恢复原样（仅余用户既有的 2 个未跟踪项）。

### 新发现并已修复的 3 个缺口（TDD，测试 `tests/test_frontend_scan.py` 6 例）

1. **JS/TS import 根解析错误**：`@/` 路径别名被当成库名（expertise 首位出现 `"@"`），scoped 包 `@scope/pkg/sub` 被截断为 `@scope`。修复：`code_scanner._js_import_root`——`@scope/pkg` 保留两段，`@/` 别名与 `./` 相对路径跳过（注意 `@/x` 按 `/` 切分首段是 `"@"` 且 truthy，必须 `startswith("@/")` 特判）。
2. **`.vue` 文件（占源码 25%）完全未扫描**：`.vue` 加入 `_CODE_EXTS` / `_GENERIC_PATTERNS` / `_IMPORT_RE` / `ext_lang`（→"vue"）。
3. **规则种子混入注释与前导引号**：`rule_seed_scanner` 增加 `line.lstrip("/*\"'\`")` 前缀清理。

另：`.vite-cache`/`.cache`/`.turbo`/`.next`/`.nuxt`/`coverage`/`.parcel-cache` 加入 `EXCLUDED_DIRS`。

### 干净全量运行结果（6 项修复全部复验有效）

- 信号：docs 326 / code 887 / conversation 189 / config 4 / tests 43 / conventions 6 / ci 1
- 知识写入 2273 条；pending_review 精确 **50**（限量生效：49 prohibition + 1 convention），archived 2223
- 禁止项种子 55 条（带行号定位，前缀清理干净）
- 代码骨架 616 模块（含 vue）；Git 500 commits / 8 人 / conventional 91%
- 画像：typescript / vue / vitest / npm / github-actions，doc moderate，test culture weak
- expertise 干净：vue, ant-design-vue, vitest, @ant-design/icons-vue, vue-i18n, dayjs, @playwright/test, pinia, vue-router
- 冲突扫描 24 文件 0 误报；stderr 全程为空；无 SQLITE_BUSY

### 遗留观察（未修，记入 backlog）

- expertise 残余弱噪声：`mock`、`"product`、`configs`、`e2e`、`playwright`——均为项目自有顶层目录的 import（如 `mock/`、`configs/`），需「项目目录感知」才能过滤，属低优先级增强。

### 关键教训

- manifest 指纹在**项目侧 `.rsi/manifest.json`**（非 RSI_HOME）——干净重跑必须同时删除项目 `.rsi/` 和临时 RSI_HOME，否则文档信号被指纹静默跳过，会跑出「假全量」。

回归基线：332 passed, 1 skipped（本轮 +6 例）。

---

## 附 3：第三轮 Python 仓库复验（2026-09-11，CalciteTest）

目标仓库：`D:\project\study\CalciteTest`（SQL 自动化测试框架，27 个 `.py` + 1575 个 YAML 测试用例；含 CLAUDE.md、pytest.ini、2 个 requirements.txt、Kerberos keytab/jar 二进制）。验证后产物已全部清理，仓库恢复原样（仅余用户既有的 3 个未跟踪项）。

### 新发现并已修复的缺口（TDD，测试 `tests/test_python_config.py` 4 例）

**Python 配置解析缺失**：仓库用 `requirements.txt` 声明依赖（pytest==7.4.3 等）且根目录有 `pytest.ini`，但画像 `test_framework`/`build_system` 全空——`config_scanner` 只认 `pyproject.toml`（requirements.txt 是最常见的 Python 依赖声明），`signal_discovery` 也不把它当配置信号；pytest 兜底推断只看 `tests/` 目录（该仓库测试与源码同目录，无 tests/）。修复：

1. `signal_discovery`：`requirements*.txt` 纳入 config 信号；
2. `config_scanner._parse_requirements`：提取依赖名（跳过注释/`-r`/`-e`/`--option`/本地路径行，`uvicorn[standard]`、`requests; marker` 归一化），映射 framework/test_framework 提示，`build_system=pip`、`language=python`；
3. pytest 兜底增加 `pytest.ini`/`tox.ini` 存在性判断。

修复后画像：`language=python, test_framework=pytest, build_system=pip`（落库验证一致）。

### 本轮复验结论（既有修复全部保持有效）

- **脱敏**：CLAUDE.md 内含明文密码 `password: dbfw#No1` 与 3 个内网 IP——全库 0 命中（`password_field` + `internal_ip` 规则生效），`.keytab`/`.jar` 二进制安全跳过
- **队列限量**：pending_review 精确 50，archived 82
- **日志**：stderr 全程为空
- **冲突扫描**：0 用户规则文件，0 误报
- **注入**：AGENTS.md 托管块创建正常（知识全 pending，块体为空属预期——无 active 知识可注入）
- 信号：docs 21 / code 27 / config 4（2 ini + 2 requirements）/ tests 4；知识写入 132 条；骨架 25 模块；Git 64 commits / 1 人

### 遗留观察修复记录（2026-09-11，用户确认后修复）

**① CLAUDE.md 等纳入规则源**（原 backlog 项）：`injector/targets.py` 新增 `discover_user_rule_files` 单一事实来源——`.cursor/rules/*.mdc`（非 rsi- 前缀）+ `.cursorrules` + `AGENTS.md` + **`CLAUDE.md`** + **`.github/copilot-instructions.md`**；`rule_seed_scanner` 与 `ConflictDetector._collect_user_rules` 均改为调用它（此前两处各自硬编码同一集合，易漂移）。实仓复验：冲突扫描文件集出现 `CLAUDE.md`。测试：`test_rule_seeds.py` +1 例、`test_conflict.py` +1 例。

**② expertise 依赖种子**（原 backlog 项）：`ConfigInsights` 新增 `dependency_names`（requirements.txt / pyproject.toml / package.json 三解析器供给，归一化小写、去重、上限 50；pom 不收集——artifactId 含项目自身坐标噪声大）；`build_profile` 在 import 强信号（≥阈值）之后用依赖名补充 expertise（stdlib/JVM 前缀过滤复用，同名去重，总量仍封顶 20）。实仓复验：expertise 从空 → 19 个真实依赖标签（pyhive/pandas/pytest/pyyaml/jinja2/paramiko/requests 等，含子项目 sql_syntax_checker 的 requirements）。测试：`test_python_config.py` +4 例。

**保留观察（未修）**：`user_profiles.user_id` 明文存 git 邮箱（身份主键性质，email 脱敏规则针对内容字段）——如需严格 PII 最小化可单列议题。

回归基线：342 passed, 1 skipped（附录 3 的 requirements.txt 修复 +4 例至 336，本次两项 +6 例至 342）。

---

## 附 4：知识保鲜——需求推翻重建/分支岔路场景（2026-09-11，用户提出）

**场景**：开发中需求被来回推翻重建、分支岔路切换，自学习知识必须跟上，不能一直积累导致冲突混乱。

### 缺口分析（spec §10.9.10「同一信号已变 → 旧条目标记过期」写了未实现）

1. **变更不收敛**：文档推翻重写后重跑，旧切片条目滞留 active/pending_review（新旧混杂）；
2. **删除归档只处理 active**：pending_review 草稿源头已删仍滞留审批队列；
3. **revert/分支切回不复活**：archived 参与去重（ISSUE-2 设计），旧内容回来时被 dedup 跳过，永远沉底；
4. **聚合类生成知识 churn**：配置摘要/Git 摘要/代码骨架/规则种子/关联图谱随开发演进，旧版本同样滞留（Git 摘要每次新 commit 必变）。

### 修复（TDD，`tests/test_knowledge_freshness.py` 7 例）

- `incremental.ingest_document`（bootstrap 全量与 watch 静默共用）：单文档按 source 三分支——命中 active/pending **保留不动**（部分修改时未变章节 id/状态/排序稳定）；命中 archived **复活**（统一回入参 status：bootstrap=pending_review 重走审批，watch=active）；未命中去重后写入；收尾 reconcile 旧版本切片 → archived。用户已裁决（rejected/suppressed）的内容尊重裁决：不复活、不重写。
- `incremental.reconcile_scope`：聚合类生成知识按标签范围收敛（signal:code/config/git/correlation/rules），内容哈希未在本次命中 → archived。
- `archive_missing_signals` 扩展到 pending_review。
- 收敛/归档/复活走裸 SQL，bootstrap 收尾统一 `knowledge.notify_changed` 补触发注入重写与检索缓存失效。
- 报告新增 `superseded`/`revived` 字段与终端行。

### 实仓验证（CalciteTest，探针文档五步循环，全程断言通过）

基线 132 条（superseded=0 无误收敛）→ 探针 v1 两节 pending → **推翻重写支付节**：旧节 archived、新节入队、未变登录节原样（同 id）→ **revert 回 v1**：旧节同 id 复活 pending、v2 节转 archived、无重复行 → **删除探针**：全部 archived。验证后产物已全部清理，仓库恢复原样。

### 设计决策记录

- **复活统一回 pending_review**（bootstrap 路径）：当前生效版本应反映「现在文件里有的」，审批队列是收敛点；`rsi_knowledge_review` 批量审批可一键恢复。
- **分支岔路不做分支隔离**：分支切换后重跑 bootstrap（或 watch 触发）即经变更收敛/删除归档自动收敛；按分支建独立知识命名空间属过度设计。
- **conversation 维度未纳入收敛**（consent 门控默认关闭，其 source 为对话文件行级定位，churn 语义不同）——记 backlog。

回归基线：349 passed, 1 skipped（342 → +7 例）。

---

## 附 5：多版本前端仓仔细扫描（2026-09-11，frontend_dbaudit_aigate）

目标：用知识保鲜机制复验一个「来回改过多个版本」的实仓。验证后产物已清理，仓库恢复原样（仅余用户既有 2 个未跟踪项）。当前分支 `codex/AiGateV3.0R26C00_harness`，近史含 8+ 次 Revert（含 Bug 252717 连续两次反向 revert）。

### 首跑结果（干净 RSI_HOME，无前序知识）

- 信号：docs 326 / code 887 / conversation 189（未授权跳过）/ config 4 / tests 43 / conventions 6 / ci 1
- 写入 2273；pending_review **50**（49 prohibition + 1 convention）；archived 2223（限量，documentation 2171 全部进 archived）
- superseded=0 / revived=0 / archived(删除)=0 —— **正确**：首跑没有「上一版知识」可收敛；保鲜机制在二次重跑/watch 时才出手
- 画像：typescript / vue / vitest；expertise 干净（vue, ant-design-vue, vitest, icons-vue, vue-i18n）
- 冲突扫描 24 个用户规则文件，detected=0（知识全非 active，冲突检测只比 active 记忆 vs 手写规则）
- stderr 空；无 SQLITE_BUSY

### 真正的多版本风险不在 RSI 库，而在源树本身（博物馆）

文首自标 **DEPRECATED / 勿再作为联调权威** 但仍被整篇切片入库：

- `docs/ops-staff-with-auth-submit-contract.md`（20 片，旧 `objectGrants` 形态）
- `docs/api/staff-permission-preview-query.md`（Mock 期接口）

现行权威 `docs/ops-role-staff-api-params.md` 明确写「旧版 contract 已 DEPRECATED，以 grantRules/extraGrantRules 为准」——两套契约并存于同一 docs/。

同功能多版本并排：

- `product/资产管理/资产目录需求文档-v1.0.md`（已确认）与 `...-v1.1.md`（未确认，批量配置语义已改）
- `product/资产管理/资产管理需求文档-v1.0.md` 与 `...-v1.1.md`
- `product/运维管理/角色权限需求文档-v1.0.md` 文件名停在 v1.0，正文已是 v1.4（含 Grant Rules 推翻 objectGrants）

日期快照型 Bug 分析（至少 6 篇，2026-08-15 ~ 09-01）：描述的问题后续 git 里大量已 fix / 再 revert，作为「当前事实」入库会与现行代码打架。

### 限量意外保护了审批队列

documentation 优先级最低，限量把 2171 篇文档（含全部废弃契约与日期快照）压进 archived，pending 只留禁止项种子。批量「恢复全部 archived」会把博物馆一次性灌进召回。

### 新缺口修复（2026-09-11，用户确认：交给用户选，不默认归档）

**做法**：多版本文档并排视为 `conflict_type=version` 冲突，**不自动归档**。`rsi_conflicts` 三选一：`keep_peer`（接受倾向，归档另一版）/ `keep_item`（推翻倾向）/ `coexist`（两版都留）。

检测（`scanner/version_conflict.py`）：
1. **文首自标废弃**：仅第一条 blockquote 以 DEPRECATED/已废弃起头才算（避免误伤「正文提到旧版已废弃」的现行权威）；并解析其链接作为现行侧。
2. **同目录 vX.Y 文件名并排**（v1.0 vs v1.1）：高版本仅作倾向。
3. **对话提示**（近 30 天 `interaction_logs.raw_input` / `retrieved_tags` 文件名命中）：谁被提及更多，倾向翻转到常被使用的一侧——用户从第一版用到终版时，MCP 记录能标出哪版还在用。无记录或平手则维持文件信号，摘录写明依据。

bootstrap 结束会跑冲突扫描，报告新增 `version_conflicts`。日期快照无兄弟文档的单篇暂不建家族（避免噪声）。IDE 对话文件仍走 `--consent`，不在此读取。

测试：`tests/test_version_conflict.py` 12 例。

---

## ISSUE-7 [P0] 跨项目串记忆

**现象**：不同仓库都装了 RSI MCP 后，召回/写入/审批会看到别的项目的约定。

**根因**（规范 §10.4 已要求每项目 `.rsi/rsi.db`，实现未落地）：
1. 所有 `rsi serve` 共用 `~/.rsi/rsi.db`
2. MCP 工具缺省 `project_id="default"`，宿主通常不传参 → 全进同一命名空间
3. bootstrap 用目录名写入，与 `default` 对不上；两个都叫 `frontend` 的仓库还会撞车

**完整闭环（2026-09-11，按 Superpowers 收口）**：
- 一个工作目录一份 `.rsi/`：记忆/画像/归档都在 `<workspace>/.rsi/rsi.db`，不写 `~/.rsi/global.db`
- 库内 `project_id` 恒为 `local`；隔离靠目录，不靠路径哈希
- 工作区发现：已有 `.rsi/` 向上认领，否则用 cwd；不爬无关 git 顶层（避免子项目写到父仓）
- `~/.rsi` 只放用户 `config.yaml`；可用 `RSI_PROJECT_ROOT` 指定工作区
- 旧版 `~/.rsi/rsi.db`：仅按目录名认领；`default` 须 `rsi migrate adopt`

测试：`tests/test_project_isolation.py`。
