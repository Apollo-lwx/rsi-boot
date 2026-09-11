# RSI Boot — 实现计划 (Plans)

> 版本：v2.6 | 日期：2026-09-10 | 状态：正式版（评审修复：+ M1 轻量价值闭环）

---

## 修订记录

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|---------|
| v2.6 | 2026-09-10 | PO (评审修复) | 响应外部评审 #1/#2：新增 P1.18 M1 轻量价值闭环（3.5d：FTS5 知识注入 + rsi_feedback 基础版 + 知识种子 CLI）——数据自 M1 起积累，消除 P3.6 落地后空等数据的空窗；P2.3 改在原路径升级（3d→2d）、P3.1 改升级完善（2d→1.5d）；M1 交付物 8→9 模块 23-26→26.5-29.5 人天；总任务 35→36 项，总工期 103-107→105-109 人天（日历 10-16 周不变） |
| v2.5 | 2026-09-10 | PO (评审修复) | 响应外部评审 #5：新增 P2.7 算法评测基线框架（2.5d，tests/eval/ + 标注数据集 + baselines.json + CI 分级）；P3.3 增收敛仿真验证（4d→4.5d）、P3.6 增门禁注入验证（6d→6.5d）；总任务 34→35 项，总工期 100-104→103-107 人天（日历 10-16 周不变） |
| v2.4 | 2026-09-10 | PO (评审修复) | 响应外部评审 #3/#4：P1.17 拆分为核心扫描层（5.5d，M2）+ 新增 P2.6 深度信号层（6.5d，Phase 2：AST/Git/对话/关联推理/增量），scanner 总工时 6-7d→12d；P2.1 扩展 VectorBackend 抽象 + brute 回退（3d→4d）；风险表新增 sqlite-vec 成熟度、scanner 复杂度、P3.6 信号依赖链三项；总任务 33→34 项，总工期 93-97→100-104 人天，日历 9-13→10-16 周 |
| v2.3 | 2026-09-10 | PO (Skill 槽补全) | 跟随 Spec v2.3：P3.6 新增技能槽运行时（config/skills/ 发现 + trigger 匹配注入），工时 5d→6d；Phase 3 交付物 20→21 人天，总工期 92-96→93-97 人天 |
| v2.2 | 2026-09-10 | PO (Genome 借鉴) | 跟随 Spec v2.2：P3.6 扩展 Genome 式配置快照（晋升快照/切换回滚/bundle 导出）与 rsi_review generate/snapshot 操作，工时 4d→5d；Phase 3 交付物 19→20 人天，总工期 91-95→92-96 人天 |
| v2.1 | 2026-09-10 | PO (Harness-RSI) | 跟随 Spec v2.1：Phase 3 新增 P3.6 Harness 自我改进提案任务（4d，learning/failure_miner.py + proposal_engine.py + review_tool.py）；覆盖统计同步为 36 条（P2 6→7），总任务 32→33 项，总工期 87-91→91-95 人天 |
| v2.0 | 2026-09-10 | PO (定位重构) | 跟随 Spec v2.0 定位转向（个人本地 MCP 工具）：**删除 P1.9 认证鉴权、P1.11 限流算法两项任务**（编号保留空缺，不重排，保证追溯矩阵引用稳定）；P1.13 Alembic 迁移改为 SQLite PRAGMA user_version 迁移；P1.1 项目骨架去除 FastAPI/docker-compose/init_db，改为 CLI 入口 + .rsi 目录初始化 + 自动建库；P1.15 去除 HTTP SSE 传输与认证握手；P1.16 配置链改四层；P1.17 存量项目专项去除团队成员子画像；P2.1 向量库改 sqlite-vec、P2.4 缓存改进程内 TTLCache；P3.2 反馈处理器改进程内异步任务；Phase 4 去除管理 UI/Grafana/Redis 缓存，改为用量统计增强与进程内缓存优化；开工前置条件去除 Docker/PostgreSQL/Redis；覆盖统计同步为 35 条（P0:14 / P1:10 / P2:6 / P3:5），总工期重算为 87-91 人天 |
| v1.2 | 2026-09-10 | PO (二次审阅修复) | 覆盖统计与 PRD §4 v1.2 统一为 37 条（P0:16 / P1:10 / P2:6 / P3:5，修正 v1.1 的 39 条误算）；工程基建任务口径与追溯矩阵同步为 1 项；P1.17 验收标准补充存量项目（场景 E）专项条件；新增非功能横切需求验证方式小节；新增「实现工作约定」（Spec 为唯一实现依据）并为全部 35 项任务标注「实现依据」Spec 章节；P1.9 权限角色残留 developer 修正为 member |
| v1.1 | 2026-09-10 | PO (审阅修复) | 修复审阅发现的问题：交付物路径与 Spec §11 目录结构全面对齐（preprocessor/、strategy/、model/providers/、api/tools/、core/ 等）；工期数字自洽化（M2 交付物 21-24 人天与明细一致；总计 90-106 人天，明确人天为纯开发工时、日历含 1.5 倍缓冲，总日历修正为 10-16 周）；角色意图清单与 Spec §3.6 统一（bug_analyze/test_data/stakeholder_comm）；反馈工具统一命名 rsi_feedback；watchdog 统一为 watchfiles；P4.2 明确为跨厂商 Fallback + 成本路由 |
| v1.0 | 2026-09-10 | PO (版本对齐) | 统一版本号为 v1.0；PRD/Specs/Plans 文档版本对齐；新增可追溯矩阵；修复编码 |
| v0.4 | 2026-09-10 | PO (终审) | 新增角色基础设施工作量详解；目录结构增加 config/roles/ 和 role_templates/；角色配置任务整合至 P1 M2 |
| v0.3 | 2026-09-10 | PO (评审汇总) | Phase 1 拆分为 M1(核心链路)/M2(网关完善)；P1.6 MCP 工时从 3d 调整为 4-5d；修复 P1.8/P1.9 编号冲突；新增 M2 认证鉴权/数据脱敏/限流/数据生命周期/Alembic 任务；新增 config_merger 配置合并模块任务；新增风险项 |

---

## 总体路线图

**团队规模**：按 2-3 人核心团队设计任务并行度。Phase 1 拆分为两个里程碑 (M1/M2)。

```
Phase 1-M1 (2-3周)    Phase 1-M2 (2-4周)    Phase 2 (2-3周)      Phase 3 (2-3周)      Phase 4 (2-3周)
核心链路                数据与协议完善         知识增强              学习闭环              优化扩展
-------                -------              -------              -------              -------
P1.1 项目骨架           P1.10 数据脱敏        P2.1 向量检索集成    P3.1 Feedback 工具    P4.1 多模型适配
P1.2 核心模型           P1.12 数据生命周期    P2.2 意图识别升级    P3.2 反馈处理器       P4.2 跨厂商Fallback+成本路由
P1.3 预处理             P1.13 SQLite迁移      P2.3 知识检索注入    P3.3 策略优化         P4.3 用量统计增强
P1.4 策略引擎           P1.14 配置热加载      P2.4 用户画像        P3.4 知识提取         P4.4 性能优化+进程内缓存
P1.5 模型适配           P1.15 MCP协议完善     P2.5 隐式反馈        P3.5 质量评估
P1.6 MCP 接口          P1.16 config_merger   P2.6 bootstrap深度信号 P3.6 Harness自我改进
|                     |                     P2.7 算法评测基线
P1.7 编排器            P1.17 rsi bootstrap 项目自学习
P1.8 后处理+日志
|P1.18 轻量价值闭环
```

> ~~P1.9 认证鉴权~~、~~P1.11 限流算法~~ 已于 v2.0 移除（本地单用户工具无此需求，见 Spec §7.2/§10.7），编号保留空缺不重排。

> 日历口径：任务人天为纯开发工时；里程碑日历 = 人天 ÷ 阶段投入人力（2-3 人）× 1.5 缓冲系数（联调/评审/测试/返工）。总工期见文末「覆盖统计摘要」。

> **实现工作约定**：Plans 只负责任务分解、工期与验收；**实现细节以 Spec v2.0 为唯一权威依据**——每个任务标注的「实现依据」章节包含必须遵守的参数、阈值、算法与数据结构（如熔断阈值 §5.2、两段式日志 §4.1、脱敏规则表 §8.1），不可仅凭本表描述实现。Plans 与 Spec 冲突时以 Spec 为准，并回写修正 Plans。

---

## Phase 1-M1：核心链路 (Core Pipeline)

**预计工期**：2-3 周 | **依赖**：Python 3.10+，无外部服务（SQLite 内嵌）

### P1.1 项目骨架搭建（3-4天）

**实现依据**：Spec §11 目录结构、§2.2 数据库表设计（自动建库与迁移）

| 任务 | 产出 | 工时 |
|------|------|------|
| Poetry 项目初始化 + pyproject.toml 配置（含 console_scripts `rsi` 入口） | 可构建、可 `pip install` 的项目骨架 | 0.5d |
| CLI 入口（rsi start / rsi init / rsi query 骨架） | __main__.py | 0.5d |
| 日志系统（JSON 格式结构化日志） | logger.py + 结构化日志 | 1d |
| `.rsi/` 数据目录初始化 + SQLite 自动建库（WAL） | data/sqlite.py, data/migrate.py | 1d |

**验收**：项目可 `pip install -e .`，`rsi start --stdio` 启动成功，`.rsi/rsi.db` 自动创建且表结构完整

### P1.2 核心数据模型（2天）

**实现依据**：Spec §2.1 核心 Pydantic 模型（字段定义、role 正则、utcnow() 约定）

| 任务 | 产出 | 工时 |
|------|------|------|
| RSIRequest + RSIResponse Pydantic 模型 | core/models.py | 0.5d |
| ContextSchema(BaseModel) + TypedDict 约束 | core/models.py | 0.5d |
| Feedback + KnowledgeItem + UserProfile 模型 | core/models.py | 0.5d |
| JSON Schema 自动生成 | schemas/ 目录下 .json 文件 | 0.5d |

**验收**：所有模型已定义，JSON Schema 文件已生成

### P1.3 预处理模块（3-4天）

**实现依据**：Spec §3.1 意图识别（INTENT_RULES 规则、置信度 0.80/0.85/0.5、顺序敏感）、§4.1 数据流程（feedback_token 在 Preprocess 生成）

| 任务 | 产出 | 工时 |
|------|------|------|
| 规则版意图识别（关键词 + 正则） | preprocessor/intent_classifier.py + config/intent_rules.yaml | 1d |
| 上下文组装（项目配置、环境信息） | preprocessor/handler.py | 1d |
| 用户画像加载（无缓存） | preprocessor/handler.py | 0.5d |
| ProcessedRequest 构建 | preprocessor/handler.py | 0.5d |
| 单元测试：test_preprocessor.py | 测试用例 | 0.5d |

**验收**：预处理模块能正确识别意图、组装上下文

### P1.4 策略引擎（3天）

**实现依据**：Spec §3.3 策略选择、§3.4 提示词渲染（模板目录 config/templates/、chat_history 10 轮截断）

| 任务 | 产出 | 工时 |
|------|------|------|
| StrategyConfig Pydantic 模型 | strategy/engine.py | 0.5d |
| 策略选择器（基于 intent + role） | strategy/engine.py | 1d |
| Jinja2 提示词渲染器 | strategy/prompt_renderer.py | 1d |
| 单元测试：test_strategy_engine.py | 测试用例 | 0.5d |

**验收**：不同意图 + 角色能选择不同策略并正确渲染提示词

### P1.5 模型适配层（3天）

**实现依据**：Spec §1.1 模型适配层（统一调用接口、流式 SSE）

| 任务 | 产出 | 工时 |
|------|------|------|
| ModelAdapter 抽象基类 + ModelResponse | model/adapter.py | 0.5d |
| OpenAIAdapter 实现（同步 + 流式） | model/providers/openai.py | 1d |
| 模型注册中心（registry） | model/registry.py | 0.5d |
| 单元测试：test_model_adapter.py | 测试用例 | 0.5d |

**验收**：能通过统一接口调用 OpenAI 并返回标准响应

### P1.6 MCP 接口层（4-5天）

**实现依据**：Spec §7 MCP 工具接口契约（JSON-RPC 方法、工具命名约定）

| 任务 | 产出 | 工时 |
|------|------|------|
| JSON-RPC 2.0 基础实现 + stdio 传输 | api/mcp_server.py | 1.5d |
| rsi_query 工具实现 | api/tools/query_tool.py | 1d |
| rsi_feedback 工具实现（桩，接收并持久化反馈） | api/tools/feedback_tool.py | 0.5d |
| tools/list + initialize 协议支持 | api/mcp_server.py | 0.5d |
| 单元测试 + 协议测试 | test_mcp_protocol.py | 0.5d |

**验收**：MCP 客户端可通过 stdio 连接并调用 rsi_query

### P1.7 编排器（3天）

**实现依据**：Spec §4.1 主请求处理流程、§5.1-§5.4 错误处理与熔断（各依赖熔断阈值、进程内内存状态、单调时钟、指数退避参数）

| 任务 | 产出 | 工时 |
|------|------|------|
| Pipeline 模式定义 | pipeline.py | 0.5d |
| Handler 基类 + 各阶段 Handler | orchestrator/handlers/ | 1d |
| 错误处理 + 重试 + 熔断器集成（进程内） | pipeline.py | 1d |
| 超时控制 + 成本核算 | pipeline.py | 0.5d |
| E2E 测试：test_pipeline.py | 测试用例 | 0.5d |

**验收**：完整请求流程走通

### P1.8 后处理 + 日志（2天）

**实现依据**：Spec §4.1 两段式日志写入（pending INSERT 先于模型调用）、§2.2 数据库表设计（SQLite DDL）、§3.7 响应格式化

| 任务 | 产出 | 工时 |
|------|------|------|
| 响应解析器（text/code/markdown 格式化） | orchestrator/handlers/postprocess.py | 0.5d |
| RSIResponse 构建 | orchestrator/handlers/postprocess.py | 0.5d |
| 交互日志记录（LogService） | services/log_service.py | 0.5d |
| 单元测试：test_postprocessor.py | 测试用例 | 0.5d |

**验收**：模型输出被正确解析为 RSIResponse，日志写入 SQLite

### P1.18 M1 轻量价值闭环（3.5天）

> v2.6 评审修复（评审 #1/#2）：M1 原交付物只是「带意图识别的 LLM 代理」，且数据积累是学习闭环唯一无法用人力压缩的时间项——反馈采集若等到 Phase 3 才上线，P3.6 落地后还需再等 4-8 周攒数据。故将闭环三个最小站点提前（每站刻意做薄），后续 Phase 在原路径上深化而非重建。

**实现依据**：Spec §3.2 知识检索（M1 为 FTS5-only 子集）、§4.2 反馈处理流程、§7.1 rsi_feedback（P0/M1）

| 任务 | 产出 | 工时 |
|------|------|------|
| FTS5 知识检索注入：knowledge_fts 表 + TopN 关键词检索 + 提示词注入点 + token 预算 | knowledge/retriever.py（FTS5 版）、strategy/prompt_renderer.py 注入点 | 2d |
| rsi_feedback 基础版：显式评分/评论落库（纯 insert，无异步处理） | api/tools/feedback_tool.py（基础版） | 1d |
| 知识种子 CLI：rsi knowledge add/list（bootstrap 在 M2，M1 手动种子） | cli/knowledge_command.py | 0.5d |

**验收**：手动添加知识条目后，匹配关键词的查询提示词中包含该条目（带来源标注、受预算截断）；rsi_feedback 评分正确落库——M1 即形成「知识注入 → 交互 → 反馈采集」最小闭环，数据自 M1 起积累

**刻意不提前**：向量检索（sqlite-vec 依赖不进 M1）、RRF 融合（P2.3 升级）、隐式反馈（P2.5）、Thompson/知识提取/Harness-RSI（Phase 3 深化）

---

## Phase 1-M2：数据与协议完善 (Data & Protocol Hardening)

**预计工期**：2-4 周 | **依赖**：P1.1~P1.8 + P1.18 + role 基础设施

### P1.10 数据脱敏（2天）

**实现依据**：Spec §8.1 数据脱敏（10 条正则模式表、落库前与外发 LLM 前双扫描时机）

| 任务 | 产出 | 工时 |
|------|------|------|
| API Key / Token 脱敏（请求/日志中自动替换） | core/masking.py | 0.5d |
| 用户敏感信息脱敏（邮箱、IP 等） | core/masking.py | 0.5d |
| 可配置脱敏规则 + 代码片段密钥扫描（落库与外发双时机） | core/masking.py | 0.5d |
| 单元测试 | tests/unit/test_masking.py | 0.5d |

### P1.12 数据生命周期（2天）

**实现依据**：Spec §8.3 数据生命周期（90 天保留/归档到 `.rsi/archive/`、卸载即删 `.rsi/`）、§4.3 离线任务

| 任务 | 产出 | 工时 |
|------|------|------|
| 日志数据过期归档清理任务（90天，导出 JSONL 后删除） | scheduler/tasks.py | 0.5d |
| 数据导出命令（JSON/CSV） | scripts/export_data.py | 0.5d |
| 知识条目删除工具（rsi_knowledge_delete） | api/tools/knowledge_tool.py | 0.5d |
| 进程内任务调度集成（asyncio 定时） | scheduler/manager.py | 0.5d |

### P1.13 SQLite 数据库迁移（1天）

**实现依据**：Spec §2.2 Schema 迁移策略（PRAGMA user_version、migrations/ 顺序脚本、幂等可重入）

| 任务 | 产出 | 工时 |
|------|------|------|
| 迁移执行器（user_version 检查 + 事务包裹顺序执行） | data/migrate.py + migrations/001_init.sql | 0.5d |
| 中断重入与幂等测试 | tests/unit/test_migrate.py | 0.5d |

### P1.14 配置热加载（2天）

**实现依据**：Spec §2.5 配置继承链（watchfiles 监听、四层优先级）

| 任务 | 产出 | 工时 |
|------|------|------|
| YAML 配置文件监听（watchfiles） | config/loader.py | 0.5d |
| 热加载事件通知机制 | config/loader.py | 0.5d |
| 运行时重载策略 | config/loader.py | 0.5d |
| 单元测试 | tests/unit/test_config.py | 0.5d |

### P1.15 MCP 协议完善（2天）

**实现依据**：Spec §7 MCP 工具接口契约（initialize 握手、tools/list、stdio 流式输出）

| 任务 | 产出 | 工时 |
|------|------|------|
| initialize 握手完善（能力协商、版本声明） | api/mcp_server.py | 0.5d |
| tools/list 完整描述（inputSchema 与 schemas/*.json 一致） | api/mcp_server.py | 0.5d |
| stdio 健壮性（大消息分帧、断连清理、背压） | api/mcp_server.py | 0.5d |
| 协议兼容性测试 | tests/unit/test_mcp_protocol.py | 0.5d |

### P1.16 config_merger 配置合并（2天）

**实现依据**：Spec §2.5 配置继承链（default → ~/.rsi/config.yaml → 项目 rsi-boot.yaml → 请求参数，与 §10.3.3 一致）

| 任务 | 产出 | 工时 |
|------|------|------|
| YAML 层级叠加（default -> 用户全局 -> project -> 请求参数） | core/config_merger.py | 0.5d |
| 环境变量覆盖 + 明文 Key 检测拒绝（§8.2） | core/config_merger.py | 0.5d |
| 类型验证 + 错误报告 | core/config_merger.py | 0.5d |
| 单元测试 | tests/unit/test_config_merger.py | 0.5d |

### P1.17 rsi bootstrap 项目自学习——核心扫描层（5.5天）

> v2.4 评审修复：原 12 组件 6-7 天的估算被评审认定为显著低估（仅 AST 代码分析一项即是可观工程）。拆分为**核心扫描层（本任务，M2）**与**深度信号层（P2.6，Phase 2）**两期，重估后合计 12 人天。

**实现依据**：Spec §10.9 项目自学习与初始化（9 类信号源、consent 同意机制、§10.9.11 分成熟度场景）

| 任务 | 产出 | 工时 |
|------|------|------|
| 信号发现引擎：自动检测 9 类信号源是否存在并生成扫描计划 | scanner/signal_discovery.py | 1d |
| 文档扫描器：docs/*.md / README / ADR -> 切片 -> 向量化 | scanner/document_scanner.py | 1d |
| 配置/规范解析器：自动识别技术栈、构建工具、测试框架、规范规则 | scanner/config_scanner.py | 0.5d |
| 初始画像生成：综合多源信号写入 UserProfile + ProjectInsights | scanner/profile_generator.py | 0.5d |
| 引导命令 CLI：rsi bootstrap --scope --dry-run --then-start | cli/bootstrap_command.py | 1d |
| 学习报告输出（终端 + JSON） | scanner/report.py | 0.5d |
| 数据验证 + 错误处理：校验/去重/敏感信息检测/断点续学 | scanner/validator.py, scanner/error_handler.py | 1d |

**验收（核心层）**：对有文档 + 配置的存量项目运行 rsi bootstrap，知识库中包含文档摘要与配置规范；画像中包含技术栈/测试习惯等基础认知；`--dry-run` 可预览扫描计划；断点续学可用。深度信号（代码骨架/Git 历史/对话上下文/跨源关联/增量监听）在 P2.6 补齐后验收

**存量项目专项（Spec §10.9.11 场景 E）**：对 3+ 年历史的存量项目——`--dry-run` 可预览信号发现结果再正式执行；废弃代码与过时文档被自动识别并降低检索权重，不污染知识库；从多年代码变迁中识别稳定架构模式与 API 演变轨迹；生成全局画像 + 项目画像双层摘要；大文件受 `--max-file-size` 限制不阻塞扫描

### 角色基础设施（横向任务，嵌入各阶段）

| P1 阶段 | 任务 | 工时加成 |
|---------|------|---------|
| M1 | RSIRequest.role 字段定义 + Developer 基础 | 0.5d（内嵌 P1.2） |
| M2 | config/roles/test.yaml + config/roles/pm.yaml；config/role_templates/ 模板 | 2d |

---

## Phase 2：知识增强 (Knowledge Enhancement)

| 任务 | 说明 | 工时 | 实现依据 (Spec) |
|------|------|------|----------------|
| P2.1 向量检索集成 (sqlite-vec) | VectorBackend 抽象 + sqlite-vec 后端 + brute 内置回退 + auto 探测 | 4d | §2.3 向量检索 Schema 与后端回退（knowledge_vectors/knowledge_fts/embedding BLOB） |
| P2.2 意图识别升级 | 从规则版升级为 ML/LLM 分类版 | 3d | §3.1 意图识别 |
| P2.3 知识检索升级 | 在 M1 FTS5 路径（P1.18）上升级：+ 向量召回 + RRF 融合 + 置信度门槛 | 2d | §3.2 知识检索（cosine ≥ 0.6 门槛、最多 5 条、2000 token 上限） |
| P2.4 用户画像 | ProfileService 完整实现 + 进程内 TTLCache | 2d | §2.1 UserProfile 模型、§3.8 画像构建与更新（推理规则/衰减/离线聚合） |
| P2.5 隐式反馈 | 采纳/修改/复制行为的自动捕获 | 3d | §4.2 反馈处理流程（客户端事件上报协议） |
| P2.6 bootstrap 深度信号层 | 代码骨架 AST（2d）+ Git 历史分析（1d）+ 对话上下文扫描（1d）+ 跨信号源关联推理（1.5d）+ 增量学习监听（1d） | 6.5d | §10.9 项目自学习（代码/ Git/对话信号源、关联推理、增量指纹） |
| P2.7 算法评测基线框架 | tests/eval/ harness + 意图/检索标注数据集构建 + baselines.json 版本化 + CI 分级接入 | 2.5d | §9 测试策略、测试计划 §12 算法验收基线 |
| 角色横向：Test/PM 角色 intent 完整注册 + 模板 | intent_rules.yaml 扩展 | 2d | §3.6 意图系统 |

## Phase 3：学习闭环 (Learning Loop)

| 任务 | 说明 | 工时 | 实现依据 (Spec) |
|------|------|------|----------------|
| P3.1 Feedback 工具完善 | 在 M1 基础版（P1.18）上升级：完整 FeedbackAction + 隐式事件对接 | 1.5d | §7.1 工具列表、§2.1 FeedbackAction |
| P3.2 反馈处理器 | 进程内异步任务消费反馈事件（asyncio.Queue） | 3d | §4.2 反馈处理流程 |
| P3.3 策略优化 | Thompson Sampling A/B 测试框架 + 收敛性仿真验证（1000 轮 × 100 重复基线） | 4.5d | §3.3 策略选择（纯采样无 epsilon、alpha/beta 更新规则）、测试计划 §12.3 |
| P3.4 知识提取 | 高质量交互 -> 知识条目（含人工确认） | 3d | §4.2 知识提取触发（diff>20%）、§4.3 离线任务 |
| P3.5 质量评估 | 基于规则 + 小模型的响应评分 | 2d | §3.5 质量评估（relevance/accuracy/actionability + 角色权重） |
| P3.6 Harness 自我改进提案 | 失败模式挖掘 + 提案生成 + 回放集回归门禁 + 技能槽运行时（config/skills/ 发现、trigger 匹配注入）+ Genome 式配置快照（晋升快照/切换回滚/bundle 导出）+ 观察期回滚 + rsi_review 工具（含 generate 按需生成与 snapshot 操作）+ 门禁注入验证（种子退化集拦截率 100%） | 6.5d | §3.9 Harness 自我改进（六槽位模型/提案生命周期/版本化快照/安全边界）、§2.2 harness_proposals 与 config_snapshots 表 |
| 角色横向：各角色独立反馈统计 | feedback 按 role 聚合分析 | 1d | §4.2 反馈处理流程 |

## Phase 4：优化扩展 (Optimization & Extension)

| 任务 | 说明 | 工时 | 实现依据 (Spec) |
|------|------|------|----------------|
| P4.1 多模型适配 | Anthropic/DeepSeek Adapter | 5d | §1.1 模型适配层 |
| P4.2 Fallback + 成本路由 | 基于成本的自动模型选择（跨厂商） | 4d | §6.1 成本核算、§5.1 熔断器 |
| P4.3 用量统计增强 | rsi_stats 报表（趋势、意图/模型分组、JSON/CSV 导出） | 2d | §6.3 用量查询 |
| P4.4 性能优化 + 进程内缓存 | TTLCache 热点请求和检索结果调优 | 3d | §2.4 进程内运行时结构 |

---

## 角色支持规划

### 内置角色意图映射

| 角色 | 内置意图 | 提示词模板 | 额外配置 |
|------|---------|-----------|---------|
| Developer | explain, debug, refactor, testing, implement, review, devops, howto, documentation | code_review.jinja2, code_gen.jinja2 等 | language, framework, code_style |
| Test Engineer | test_gen, test_review, bug_analyze, test_data, coverage_check | test_gen.jinja2, bug_analysis.jinja2 | test_framework, report_format |
| Product Manager | req_analyze, prd_gen, doc_review, data_analysis, stakeholder_comm | prd_gen.jinja2, doc_review.jinja2 | doc_template, output_format |

> 意图清单以 Spec §3.6 为唯一权威来源；M1 规则版通用意图（code_review/code_gen/debug/explain/general_assist）与角色意图的映射关系见 Spec §3.1。

### Phase 与角色支持对应

| Phase | Developer | Test Engineer | Product Manager | 自定义角色 |
|-------|-----------|---------------|-----------------|-----------|
| P1 M1 | 完整支持 | 通用兜底 | 通用兜底 | - |
| P1 M2 | 稳定 | 基础支持 (test_gen) | 基础支持 (prd_gen) | - |
| P2 | 完善 | 角色 intent 注册 | 角色 intent 注册 | 通过配置注册 |
| P3 | 完善 | 独立反馈统计 | 独立反馈统计 | 独立反馈统计 |
| P4 | 完整 | 完整 | 完整 | 完整 |

### 自定义角色扩展路径

1. 创建 `config/roles/{role_name}.yaml` 定义意图规则
2. 在 `config/role_templates/{role_name}.jinja2` 放置模板
3. 创建项目知识条目时指定 role 和 domain 字段
4. 系统自动学习该角色的使用模式
5. 无需修改代码，完全通过配置扩展

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|----------|
| OpenAI API 不稳定/限流（单点故障） | 中 | 高 | 熔断器 + 重试；Phase 2 实现同厂商型号 fallback（gpt-4o → gpt-4o-mini），Phase 4 扩展跨厂商 |
| 知识检索效果不如预期 | 中 | 中 | Phase 2 预留调优时间；FTS5/向量混合检索参数可调 |
| 反馈率过低影响学习效果 | 高 | 中 | M1 起即采集显式反馈与交互日志（P1.18），为 Phase 3 预攒数据；优先完善隐式反馈（目标 80%）；Phase 3 用模拟数据验证 |
| SQLite 可靠性（并发写/文件损坏） | 低 | 中 | WAL 模式 + 写锁重试；启动 `PRAGMA integrity_check` 自检；损坏时自动备份重建 |
| 知识库冷启动 | 中 | 中 | 提供种子脚本；rsi bootstrap 一键扫描学习项目 |
| sqlite-vec 成熟度（社区小、平台 wheel 覆盖与大规模基准有限） | 中 | 中 | VectorBackend 抽象 + brute 内置回退（零迁移，§2.3）；CI 三平台矩阵；FAISS 为可选未来后端 |
| bootstrap scanner 复杂度低估（AST/关联推理等重组件） | 高 | 中 | v2.4 已分层：核心扫描层（M2）+ 深度信号层（P2.6），深度层延期不阻塞 M2 交付与冷启动主流程 |
| P3.6 依赖前序信号质量（P3.1-P3.5 反馈/知识/评估数据不足则挖掘与回归无法运行） | 中 | 高 | Phase 3 前半段用模拟数据验证挖掘管线；回归门禁回放集不足 50 条时提案暂缓并提示积累量 |

---

## 团队规模假设

本项目按 **2-3 人核心团队** 设计任务并行度（日历 = 人天 ÷ 人力 × 1.5 缓冲系数）。
- **2 人团队**：M1 预计 3-4 周，M2 预计 2-3 周
- **3 人团队**：M1 预计 2-3 周，M2 预计 2 周
- 单人开发日历时间需加倍

## Phase 1 开工前置条件

- [x] Python 3.10+ 已安装
- [x] Poetry 包管理器已安装
- [ ] sqlite-vec 扩展可用（pip 包 sqlite-vec，Phase 2 首选后端；缺失时 brute 后端自动回退，不阻塞）
- [ ] OpenAI API Key 已配置（环境变量）
- [ ] 项目目录已初始化

---

## 交付物清单（按 Phase/里程碑组织）

### Phase 1-M1 交付物（9 个模块，26.5-29.5 人天）

| 模块 | 交付物文件 | 关联 PRD 需求 | 关联 Specs 章节 |
|------|-----------|-------------|----------------|
| 项目骨架 | pyproject.toml, __main__.py, data/sqlite.py, data/migrate.py, migrations/001_init.sql | §4.1-M1 基础设施 | §11 目录结构, §2.2 数据库表设计 |
| 核心模型 | core/models.py, schemas/*.json | §4.1-M1 标准化格式 | §2.1 核心 Pydantic 模型 |
| 预处理 | preprocessor/handler.py, preprocessor/intent_classifier.py, config/intent_rules.yaml | §4.1-M1 意图识别 | §3.1 意图识别, §4.1 数据流程 |
| 策略引擎 | strategy/engine.py, strategy/prompt_renderer.py | §4.1-M1 路由决策 | §3.3 策略选择, §3.4 提示词渲染 |
| 模型适配 | model/adapter.py, model/providers/openai.py, model/registry.py | §4.1-M1 模型接入 | §1.1 模型适配层 |
| MCP 接口 | api/mcp_server.py, api/tools/query_tool.py | §4.1-M1 MCP 协议 | §7 MCP 接口契约 |
| 编排器 | orchestrator/pipeline.py, orchestrator/handlers/* | §4.1-M1 流程编排 | §4.1 主请求处理流程 |
| 后处理+日志 | orchestrator/handlers/postprocess.py, services/log_service.py | §4.1-M1 输出处理 | §3.5 质量评估, §2.2 数据库表设计 |
| 轻量价值闭环 | knowledge/retriever.py（FTS5 版）, api/tools/feedback_tool.py（基础版）, cli/knowledge_command.py | §4.2 知识增强（M1 子集）, §4.3 学习闭环（采集先行） | §3.2 知识检索, §4.2 反馈处理流程 |

### Phase 1-M2 交付物（8 个模块，18-19 人天）

| 模块 | 交付物文件 | 关联 PRD 需求 | 关联 Specs 章节 |
|------|-----------|-------------|----------------|
| 数据脱敏 | core/masking.py | §6 非功能需求 | §8.1 数据脱敏 |
| 数据生命周期 | scheduler/tasks.py, scripts/export_data.py | §6 非功能需求 | §8.3 数据生命周期 |
| SQLite 迁移 | migrations/*, data/migrate.py | 工程基建 | §2.2 Schema 迁移策略 |
| 配置热加载 | config/loader.py | 工程基建 | §2.5 配置继承链 |
| MCP 协议完善 | api/mcp_server.py (扩展) | §4.1-M1 MCP | §7 MCP 接口契约 |
| config_merger | core/config_merger.py | 配置管理 | §2.5 配置继承链 |
| rsi bootstrap（核心扫描层） | scanner/signal_discovery.py, document_scanner.py, config_scanner.py, profile_generator.py, validator.py, report.py, cli/bootstrap_command.py | §9.3 存量项目自学习 | §10.9 项目自学习与初始化 |
| 角色基础设施 | config/roles/*.yaml, config/role_templates/* | §2 目标用户 | §3.6 意图系统, §3.7 格式化 |

### Phase 2 交付物（8 个模块，25 人天）
| 模块 | 交付物文件 | 关联 PRD 需求 | 关联 Specs 章节 |
|------|-----------|-------------|----------------|
| 向量检索集成 | data/vec.py（VectorBackend 抽象 + sqlite-vec/brute 双后端）, knowledge/embedding.py | §4.2 知识增强 | §2.3 向量检索 Schema 与后端回退 |
| bootstrap 深度信号层 | scanner/code_scanner.py, git_analyzer.py, conversation_scanner.py, correlation_engine.py, incremental.py | §9.3 存量项目自学习 | §10.9 项目自学习与初始化 |
| 意图升级 | preprocessor/intent_classifier.py | §4.2 知识增强 | §3.1 意图识别 |
| 知识检索注入 | knowledge/retriever.py | §4.2 知识增强 | §3.2 知识检索（混合检索） |
| 用户画像 | services/profile_service.py | §4.2 知识增强 | §2.1 UserProfile 模型 |
| 隐式反馈 | feedback/implicit_tracker.py（含客户端上报协议对接） | §4.3 学习闭环 | §4.2 反馈处理流程 |
| 算法评测基线 | tests/eval/（harness + datasets + baselines.json） | 工程质量基建 | §9 测试策略、测试计划 §12 |
| 角色扩展 | config/roles/ 扩展 | §2 目标用户 | §3.6 意图系统 |

### Phase 3 交付物（7 个模块，21.5 人天）
| 模块 | 交付物文件 | 关联 PRD 需求 | 关联 Specs 章节 |
|------|-----------|-------------|----------------|
| Feedback 工具 | api/tools/feedback_tool.py | §4.3 学习闭环 | §7.1 工具列表 |
| 反馈处理器 | feedback/task.py | §4.3 学习闭环 | §4.2 反馈处理流程 |
| 策略优化 | strategy/thompson_sampler.py | §4.3 学习闭环 | §3.3 策略选择 |
| 知识提取 | learning/knowledge_extractor.py | §4.3 学习闭环 | §4.3 离线分析任务 |
| 质量评估 | core/quality_evaluator.py | §5 成功指标 | §3.5 质量评估 |
| Harness 自我改进 | learning/failure_miner.py, learning/proposal_engine.py, api/tools/review_tool.py | §4.3 学习闭环 | §3.9 Harness 自我改进 |
| 反馈统计 | learning/feedback_analyzer.py | §4.3 学习闭环 | §4.2 反馈处理流程 |

### Phase 4 交付物（4 个模块，14 人天）
| 模块 | 交付物文件 | 关联 PRD 需求 | 关联 Specs 章节 |
|------|-----------|-------------|----------------|
| 多模型适配 | model/providers/anthropic.py, model/providers/deepseek.py | §4.4 优化扩展 | §1.1 模型适配层 |
| 跨厂商 Fallback + 成本路由 | strategy/cost_router.py | §4.4 优化扩展 | §6.1 成本核算, §5.1 熔断器 |
| 用量统计增强 | api/tools/stats_tool.py | §4.4 优化扩展 | §6.3 用量查询 |
| 性能优化 + 进程内缓存 | data/cache 调优 | §4.4 优化扩展 | §2.4 进程内运行时结构 |

---

## PRD 需求覆盖全景

### 覆盖统计摘要

| 维度 | 统计 |
|------|------|
| PRD 需求总数 | 36 条（P0: 14, P1: 10, P2: 7, P3: 5，优先级以 PRD §4 v2.1 为准；详见追溯矩阵） |
| Plans 已覆盖 | **36/36（100%）** |
| P0 覆盖率 | **14/14（100%）** - 全部 Core Pipeline + Data & Protocol Hardening |
| P1 覆盖率 | **10/10（100%）** - 角色基础设施 + Knowledge Enhancement + rsi bootstrap |
| P2 覆盖率 | **7/7（100%）** - 全部 Learning Loop（含 Harness 自我改进提案） |
| P3 覆盖率 | **5/5（100%）** - 全部 Optimization & Extension（含「团队共享模式（刻意不做）」决策记录） |
| 工程基建任务（非 PRD 需求） | 1 项（项目骨架 P1.1；SQLite 迁移/配置热加载/config_merger 已归入 PRD 需求，见追溯矩阵） |
| Plans 总任务数 | 36 项（含角色基础设施横向任务 + rsi bootstrap 两层；P1.9/P1.11 已于 v2.0 移除） |
| 总工期估算 | 105-109 人天（纯开发工时）；按 2-3 人核心团队、含 1.5 倍联调/评审/测试缓冲，总日历约 10-16 周 |

> 人天明细：M1 26.5-29.5 + M2 18-19 + P2 25 + P3 21.5 + P4 14。

### 非功能横切需求（隐式覆盖，由既有任务承载）

以下 5 条为架构横切属性，不设独立任务，实现时须通过对应验证方式确认（与追溯矩阵 v2.0 一致）：

| 非功能需求 | 承载任务 | 验证方式 |
|-----------|---------|---------|
| SSE 流式响应 | P1.5（模型流式调用）+ P1.15（stdio 流式输出） | 测试计划 ADA-06 |
| 本地零依赖部署 | 架构原生（SQLite 内嵌，无外部服务） | 全新机器 pip 安装验证 |
| 可观测性（结构化日志） | P1.1（日志系统） | 日志格式检查 |
| 可测试性（核心模块契约可 mock） | 架构设计 + 测试计划 | 单元测试覆盖率门禁 |
| 向后兼容（工具接口版本化） | §7.1 命名约定 + RSIResponse.api_version | 契约测试 |

### 结论

> ✅ **按此 Plans 实现，可完整覆盖 PRD v2.1 定义的全部 36 条 P0/P1/P2/P3 需求；5 条非功能横切需求由既有任务承载并按上表验证。**

---

> **文档信息**
> - 版本：v2.6
> - 最后更新：2026-09-10
> - 下一阶段：Phase 1-M1 实现
