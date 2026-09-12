# RSI Boot — 测试计划

> 版本：v2.6 | 日期：2026-09-10 | 状态：正式版（评审修复：+ M1 轻量价值闭环）

---

## 修订记录

| 版本 | 日期 | 修订人 | 变更内容 |
|------|------|--------|----------|
| v2.6 | 2026-09-10 | QA (评审修复) | 响应外部评审 #1/#2：§2.9/§2.10 标注 M1 轻量版（P1.18）与后续 Phase 的用例适用边界（FTS5 子集 / 反馈基础版） |
| v2.5 | 2026-09-10 | QA (评审修复) | 响应外部评审 #5：新增 §12 算法质量评估与验收基线——意图分类（标注集 ≥50 条/意图，规则版 ≥85%/LLM 版 ≥92%）、知识检索（Recall@5 ≥80%/MRR ≥0.65，3 类项目泛化，网格调优归档）、Thompson 收敛（仿真 1000 轮最优臂 ≥90%、regret 优于随机 ≥40%）、Harness 提案（最小化五项客观标准、门禁注入拦截 100%、首批接受率 ≥50%、回滚率 >30% 告警）、画像推断（≥80%）、评测基础设施 tests/eval/ 与 CI 分级（per-PR 快速门槛 / nightly 全量 / baselines.json 版本化）；原 §12 风险与应对顺移 §13 |
| v2.4 | 2026-09-10 | QA (评审修复) | 跟随 Spec/Plans v2.4：KNO-09 改写为三级回退链（sqlite-vec→brute→纯 FTS5），新增 KNO-10 双后端 TopK 一致性（≥80%）与 KNO-11 后端配置强制；风险表 sqlite-vec 行同步回退策略 |
| v2.3 | 2026-09-10 | QA (Skill 槽补全) | 跟随 Spec v2.3：§2.14 新增 HRSI-16/17——技能槽提案生效（trigger 匹配注入 + 来源标注 + 进快照）、技能热加载与停用 |
| v2.2 | 2026-09-10 | QA (Genome 借鉴) | 跟随 Spec v2.2：§2.14 新增 HRSI-11~15——晋升即配置快照、回滚 = 切换快照、快照合并语义（缺省继承/null 重置/有值覆盖）、rsi_review generate 按需生成、快照导出 bundle |
| v2.1 | 2026-09-10 | QA (Harness-RSI) | 跟随 Spec v2.1：新增 §2.14 Harness 自我改进用例系列（HRSI-01~10）：失败挖掘分桶、提案最小化约束、回归门禁通过/否决、人工确认晋升、观察期自动回滚、手动回滚、速率硬上限、元级修改禁止、事实日志 append-only 验证 |
| v2.0 | 2026-09-10 | QA (定位重构) | 跟随 Spec v2.0 定位转向：**删除 §2.8 限流（RAT 系列 6 条）与 §2.9 认证鉴权（AUTH 系列 10 条）两整节**；熔断用例（CIR）改写为 sqlite/embedding/llm 三依赖与进程内冷启动语义；MCP 删除 HTTP 传输用例；反馈用例改 asyncio.Queue 进程内语义（重启丢失可接受、显式反馈同步落库）；预算用例改每日 token 上限（§6.2）；压力测试规模下调至个人工具量级并去除 QPS/吞吐指标；混沌测试故障面改 SQLite/Embedding/LLM；安全测试删除管理 API/暴力破解/TLS 服务端项；升级测试改 PRAGMA user_version 迁移；测试环境去除 testcontainers/docker-compose，改临时目录 SQLite + stdio 子进程 |
| v1.1 | 2026-09-10 | QA (审阅修复) | 与 Spec v1.1 全面校准：意图名/置信度对齐 §3.1 规则版（code_gen/code_review/general_assist）；限流用例对齐 §8.3（60 req/min + 桶容量 60）；质量评估维度对齐 §3.5（relevance/accuracy/actionability）；Thompson 用例对齐 §3.3（纯采样无 epsilon，reward 数值与 alpha/beta 更新规则一致）；熔断用例指明各服务阈值（§5.2）；CHA-07 改为单调时钟验证；AUTH 系列区分 X-API-Key/JWT 与 401/403 语义；CON-01 对齐两段式日志写入；补全测试金字塔图；覆盖率口径注明 |
| v0.1 | 2026-09-10 | QA | 初稿 |

---

## 1. 测试策略总览

### 测试金字塔

```
     /\
    /  \   E2E 测试 (5%)      — stdio 子进程启动真实 MCP 调用
   /----\
  /      \ 集成测试 (20%)     — Pipeline 全链路 + 临时目录 SQLite + mock LLM
 /--------\
/          \ 单元测试 (60%)   — 各模块核心逻辑 + 算法
\          / 契约测试 (10%)   — MCP 工具定义 vs 实现一致性
 \--------/  混沌测试 (5%)    — 熔断器 / 超时 / 降级 / 容错
```

> 全部测试零外部服务依赖：SQLite 用 tmp_path 临时文件，LLM/Embedding 用 mock，可在任何 CI 裸机运行。

## 2. 测试用例

### 2.1 编排器 Pipeline

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| PIPE-01 | 正常流程：5 个 Handler 依次执行 | mock LLM 可用 | 1. 发送 rsi_query 请求 | 预处理->策略->模型->后处理->日志 按序执行，返回 RSIResponse | P0 |
| PIPE-02 | Handler 抛出异常 | PreprocessHandler 抛异常 | 1. 发送请求 2. 模拟预处理失败 | 编排器捕获异常，返回 error 状态响应，不继续后续 Handler | P0 |
| PIPE-03 | Handler 超时 | ModelCallHandler 模拟 30s 延迟 | 1. 发送请求 2. Handler 超时 | 编排器中断该 Handler，跳过后处理直接返回超时错误 | P0 |
| PIPE-04 | Handler 链中断 | PostprocessHandler 抛异常 | 1. 发送请求 2. 后处理异常 | 日志 Handler 仍需执行记录异常信息 | P1 |
| PIPE-05 | 空 Handler 链 | 无注册 Handler | 1. 请求进入编排器 | 返回错误，提示无可用 Handler | P2 |
| PIPE-06 | 并发请求下 Pipeline 隔离性 | 20 并发请求 | 1. 并发发送请求 2. 各请求不同参数 | 每个请求独立通过 Handler 链，互不干扰 | P0 |


### 2.2 预处理 Preprocessor

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| PRE-01 | 意图识别：code_gen 类型 | 输入包含"write a function" | 1. 调用 detect_intent | 返回 intent=code_gen, confidence=0.80（Spec §3.1 规则版） | P0 |
| PRE-02 | 意图识别：debug 类型 | 输入包含"fix this bug" | 1. 调用 detect_intent | 返回 intent=debug, confidence=0.85 | P0 |
| PRE-03 | 意图识别：code_review 类型 | 输入"review this" | 1. 调用 detect_intent | 返回 intent=code_review, confidence=0.85 | P0 |
| PRE-04 | 意图识别：无匹配回退 | 输入"hello world" | 1. 调用 detect_intent | 返回 intent=general_assist, confidence=0.5 | P0 |
| PRE-05 | 意图识别：多规则匹配（顺序敏感） | 输入"fix bug and write test" | 1. 调用 detect_intent | 规则按 INTENT_RULES 列表顺序先匹配：code_gen（含 "write"）先命中，返回 code_gen(0.80) | P0 |
| PRE-06 | 用户画像加载：用户存在 | 库中该 user_id 有 profile | 1. 发送请求 2. 预处理加载 | ProcessedRequest.user_profile 非空 | P1 |
| PRE-07 | 用户画像加载：用户不存在 | 库中无该 user_id | 1. 发送请求 2. 预处理加载 | ProcessedRequest.user_profile=None，使用默认画像 | P1 |
| PRE-08 | 项目配置加载：多级继承 | 默认+用户全局+项目 三级配置 | 1. 发送请求（有 project_id） | ProcessedRequest.merged_config 正确合并三级配置（§2.5） | P1 |
| PRE-09 | context 输入超长 | raw_input=32769 字符 | 1. 发送请求 | Pydantic 校验拒绝，返回校验错误 | P0 |
| PRE-10 | 空输入 | raw_input="" | 1. 发送请求 | Pydantic 校验拒绝（min_length=1） | P0 |
| PRE-11 | user_id 自动解析 | 未显式传 user_id | 1. git config user.email 存在时发送请求 | user_id 取 git email；无 git 配置时为 `local`（§7.2） | P1 |


### 2.3 策略引擎 StrategyEngine

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| STR-01 | 单策略匹配 | 该 intent 有 1 个活跃策略 | 1. select_strategy(intent) | 返回该策略配置 | P0 |
| STR-02 | 无匹配策略 | 该 intent 无活跃策略 | 1. select_strategy(intent) | 返回 default_strategy(intent) | P0 |
| STR-03 | A/B 测试多策略 | 该 intent 有 2 个活跃策略 | 1. 调用 100 次 | 两个策略均被选中，分布接近配置权重 | P1 |
| STR-04 | 模板渲染：完整变量 | 所有模板变量均有值 | 1. render_prompt() | 模板中所有 {{ var }} 被替换 | P0 |
| STR-05 | 模板渲染：缺失变量 | 某模板变量未提供 | 1. render_prompt() | 缺失变量保留原样或替换为空字符串 | P1 |
| STR-06 | 模板渲染：知识超长 | retrieved_docs 总 token 超上限 | 1. render_prompt() | 按 Spec §3.4：知识注入上限 2000 token，低分条目被裁剪 | P0 |
| STR-07 | 模板渲染：历史超 10 轮 | chat_history > 10 轮 | 1. render_prompt() | 按 Spec §3.4：保留最近 10 轮，超出部分截断 | P1 |
| STR-08 | Thompson Sampling：低样本自然探索 | 某策略样本稀少（alpha/beta 接近先验） | 1. 调用 thompson_sample() 多次 | 该策略因 Beta 分布方差大仍有可观被选概率（纯采样，无强制探索机制，Spec §3.3） | P2 |
| STR-09 | Thompson Sampling：利用阶段 | 各策略样本充足 | 1. 调用 thompson_sample() 多次 | 高 accept_rate 策略被选中的概率显著更高 | P2 |

### 2.4 模型适配 ModelAdapter

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| ADA-01 | OpenAI 正常调用 | API Key 有效（mock） | 1. 构造 Prompt 2. 调用 generate() | 返回 ModelResponse，status=success | P0 |
| ADA-02 | API 超时 | 模拟 65s 超时 | 1. 调用 generate() 2. 达到超时时间 | 抛出 TimeoutError | P0 |
| ADA-03 | API rate limit | 模拟 429 响应 | 1. 调用 generate() | 抛出 RateLimitError，可重试 | P0 |
| ADA-04 | API Key 无效 | 模拟 401 响应 | 1. 调用 generate() | 抛出 AuthError，不可重试 | P0 |
| ADA-05 | 返回格式异常 | 模拟非 JSON 响应 | 1. 调用 generate() | 返回原始文本，标记 format=raw | P1 |
| ADA-06 | 流式响应 SSE | 开启 stream=True | 1. 调用 generate(stream=True) | 逐 chunk 返回内容，最后返回完整响应 | P0 |
| ADA-07 | Fallback 切换 | 主模型失败，有 fallback | 1. generate_with_fallback() | 主模型失败后自动调用 fallback 模型 | P1 |

### 2.5 后处理 Postprocessor

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| POS-01 | 解析纯文本输出 | 模型返回纯文本 | 1. parse_output() | ContentItem[0].type=text | P0 |
| POS-02 | 解析代码块输出 | 模型返回含 markdown 代码块 | 1. parse_output() | ContentItem 含 type=code，language 正确识别 | P0 |
| POS-03 | 解析混合输出 | 模型返回文本+代码+JSON | 1. parse_output() | 多个 ContentItem，顺序正确 | P0 |
| POS-04 | 解析失败回退 | 模型返回不可解析内容 | 1. parse_output() | 宽松解析，纯文本兜底，不抛异常 | P1 |
| POS-05 | 质量评估：高分场景 | response 高度相关+完整+合规 | 1. evaluate_quality() | quality_score >= 0.8 | P2 |
| POS-06 | 质量评估：低分场景 | response 不相关+不完整 | 1. evaluate_quality() | quality_score <= 0.4 | P2 |
| POS-07 | feedback_token 生成 | 正常流程 | 1. 生成 token | token 为唯一字符串，可关联请求 | P0 |
| POS-08 | 上下文裁剪：超限 | messages > context_window | 1. truncate_context() | 按 Spec §3.4：保留 system prompt + 最近 10 轮历史，更早的截断 | P1 |


### 2.6 MCP 接口

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| MCP-01 | initialize 握手 | 客户端连接 | 1. 发送 initialize 请求 | 返回 ServerCapabilities，含协议版本 | P0 |
| MCP-02 | tools/list | 初始化完成 | 1. 发送 tools/list | 返回工具列表，至少包含 rsi_query；inputSchema 与 schemas/*.json 一致 | P0 |
| MCP-03 | tools/call rsi_query | 工具已注册 | 1. 发送 tools/call rsi_query | 返回 RSIResponse | P0 |
| MCP-04 | 协议版本不匹配 | 客户端版本 < 服务端 | 1. 发送 initialize | 返回错误提示，建议升级 | P0 |
| MCP-05 | 非法 method | 发送不存在的 method | 1. 发送 {"method": "invalid"} | 返回 MethodNotFound 错误 | P0 |
| MCP-06 | 未初始化直接调用工具 | 连接后直接 tools/call | 1. 发送 tools/call | 返回 ServerNotInitialized 错误 | P0 |
| MCP-07 | stdio 传输 | 子进程启动 | 1. stdin 写入 JSON-RPC | stdout 输出 JSON-RPC 响应 | P0 |
| MCP-08 | stdio 健壮性 | 大消息/异常断连 | 1. 发送超大消息 2. 中途 kill 客户端 | 大消息正确处理；断连后进程干净退出，无资源泄漏 | P1 |

### 2.7 熔断器 CircuitBreaker

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| CIR-01 | CLOSED→OPEN 转换 | sqlite 熔断器（阈值 3，§5.2） | 1. 模拟连续 3 次失败 2. 检查状态 | 状态变为 OPEN（注：各依赖阈值不同——sqlite 3 / embedding 3 / llm 5） | P0 |
| CIR-02 | OPEN→HALF_OPEN 转换 | sqlite 熔断器（恢复超时 30s） | 1. 设置 OPEN 2. 等待 30s | 状态自动变为 HALF_OPEN（llm 60s 需分别验证） | P0 |
| CIR-03 | HALF_OPEN→CLOSED 恢复 | 探测请求成功 | 1. HALF_OPEN 下放行 1 个探测成功 2. 检查状态 | 状态恢复 CLOSED，计数清零 | P0 |
| CIR-04 | HALF_OPEN→OPEN 再次断开 | HALF_OPEN 下探测失败 | 1. 设置 HALF_OPEN 2. 模拟失败 | 状态回到 OPEN 并重新计时 recovery_timeout | P0 |
| CIR-05 | OPEN 状态快速失败 | 状态为 OPEN | 1. 调用 generate() | 不实际发起请求，直接抛 CircuitOpenError 走降级链 | P0 |
| CIR-06 | 纯进程内语义 | 熔断器为内存对象（§2.4/§5.1） | 1. 触发 OPEN 2. 检查实现 | 状态存内存对象属性，无任何外部存储依赖 | P0 |
| CIR-07 | 进程重启冷启动 | 重启进程 | 1. 重启 2. 检查熔断器 | 全部从 CLOSED 冷启动，failure_count=0 | P1 |
| CIR-08 | 计数器重置 | OPEN→CLOSED 后 | 1. 状态恢复后检查 failure_count | failure_count 重置为 0 | P1 |


### 2.8 数据脱敏 Sanitizer

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| SAN-01 | API Key 脱敏 | raw_input 含 "sk-..." | 1. 发送含 API Key 的请求 2. 检查日志 | 日志中 API Key 被替换为 sk-**** | P1 |
| SAN-02 | 嵌套 JSON 脱敏 | 内容含嵌套敏感字段 | 1. 发送含嵌套敏感数据的请求 | 所有敏感字段均被脱敏 | P1 |
| SAN-03 | 非敏感内容保持 | 正常代码输入 | 1. 发送纯代码请求 | 日志内容不变 | P1 |
| SAN-04 | 脱敏性能影响 | 大量数据 | 1. 发送大请求 2. 测量处理时间 | 脱敏耗时 < 5ms（Spec §8.1 性能预算） | P2 |
| SAN-05 | 外发 LLM 前扫描 | prompt 组装后含密钥模式 | 1. 构造含密钥的知识条目并触发注入 | 外发 prompt 中密钥已脱敏（§8.1 双时机） | P0 |
| SAN-06 | 十条正则全覆盖 | 构造命中每条 MASKING_RULES 的样本 | 1. 逐条执行扫描 | email/ipv4/openai_key/aws_akid/github_token/slack_token/jwt/private_key/password_field/internal_ip 全部命中且替换正确 | P1 |

### 2.9 知识检索 KnowledgeRetrieval (M1 FTS5 子集 / Phase 2 混合)

> M1（P1.18）交付 FTS5-only 子集：KNO-01~03 中向量相关步骤以 FTS5 结果为准，KNO-09~11 自 Phase 2 起适用。

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| KNO-01 | 混合检索：正常场景 | 知识库有 100 条条目 | 1. 搜索 "Python 函数" | 返回 top-5，FTS5+向量 RRF 融合排序 | P1 |
| KNO-02 | 空知识库 | 知识库为空 | 1. 搜索任何关键词 | 返回空列表 | P1 |
| KNO-03 | Embedding 服务超时 | embedding 熔断 OPEN | 1. 搜索 | 降级为纯 FTS5 检索返回结果，不抛异常（§5.4 Level 1） | P1 |
| KNO-04 | 项目隔离 | projectA 和 projectB 各有知识 | 1. 指定 project_id 搜索 | 只返回该项目的条目 | P1 |
| KNO-05 | 较大数据量 | 1 万条条目 | 1. 搜索 2. 测量延迟 | P99 检索 < 500ms | P1 |
| KNO-06 | 标签过滤 | 条目含多标签 | 1. 按标签过滤搜索 | 只返回匹配标签的条目 | P2 |
| KNO-07 | 知识条目 CRUD | 新增条目 | 1. 创建知识条目 2. 读取 3. 更新 4. 删除 | 所有操作正确，向量与 FTS 同事务同步更新（§2.3） | P1 |
| KNO-08 | 嵌入缓存命中 | 相同 query 两次 | 1. 搜索 query A 2. 再次搜索 query A | 第二次命中缓存，延迟更低 | P2 |
| KNO-09 | sqlite-vec 缺失回退 | 扩展未安装 | 1. 启动并搜索 | auto 探测切 brute 后端（BLOB 暴力余弦），语义排序保留、零数据迁移；brute 亦不可用时才降级纯 FTS5（§2.3 回退链） | P1 |
| KNO-10 | 双后端结果一致性 | sqlite-vec 可用 | 1. 同一查询分别走 vec0 与 brute | TopK 结果重合度 ≥ 80%（浮点误差容忍），排序质量等价 | P2 |
| KNO-11 | 后端配置强制 | vector.backend=brute | 1. 启动并搜索 | 跳过 vec0 直接使用 brute；启动日志记录激活后端 | P2 |


### 2.10 反馈闭环 Feedback (M1 基础版 / Phase 3 完整)

> M1（P1.18）交付 rsi_feedback 基础版：仅显式评分/评论同步落库适用；异步消费、隐式事件、画像/策略联动自 Phase 2/3 起适用。

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| FDB-01 | 显式反馈提交：采纳 | 有 feedback_token | 1. 提交 action=accepted 2. 处理 | 日志状态更新，策略 accept_count+1 | P1 |
| FDB-02 | 显式反馈提交：修改 | 有 modified_content | 1. 提交 action=modified | 日志更新；diff 比例 >20% 时触发知识候选提取（阈值定义见 Spec §4.2） | P1 |
| FDB-03 | 隐式反馈：采纳（未修改） | 用户接受建议未修改 | 1. 行为推断 2. 提交隐式反馈 | 策略 accept_count+1 | P1 |
| FDB-04 | 隐式反馈：复制 | 用户复制响应内容 | 1. 事件监听 2. 提交 COPIED | 质量评分加权 | P2 |
| FDB-05 | 无效 feedback_token | token 不存在 | 1. 提交无效 token | 返回错误，提示 token 无效 | P1 |
| FDB-06 | 重复提交反馈 | 同一 token 提交两次 | 1. 首次提交 2. 再次提交 | log 记录更新，但统计不重复累加 | P1 |
| FDB-07 | 反馈异步处理 | asyncio.Queue 积压 | 1. 批量提交 100 条反馈 | 队列消费平稳，主链路不阻塞 | P2 |
| FDB-08 | 进程退出未处理反馈 | 队列中有未消费消息 | 1. 退出进程 2. 检查 | 队列中增量更新丢失可接受；显式反馈（rating/rejected）已在响应返回前同步落库（§4.2） | P1 |

### 2.11 Thompson Sampling 策略优化 (Phase 3)

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| THO-01 | 冷启动：所有臂平等 | 新策略，alpha=beta=1（均匀先验） | 1. 调用 thompson_sample() 多次 | 各臂被选概率接近均等（大方差自然探索，Spec §3.3） | P2 |
| THO-02 | 积累后利用 | 臂A: 8/10采纳, 臂B: 2/10采纳 | 1. 多次调用 2. 统计分布 | 臂A被选中概率显著高于臂B | P2 |
| THO-03 | 探索自然发生（无 epsilon 机制） | 臂A 样本充足高采纳，臂B 样本稀少 | 1. 统计 1000 次选择 | 臂B 因 Beta 分布方差大仍有一定被选概率（纯 Thompson 特性，不依赖 epsilon） | P2 |
| THO-04 | Beta 分布更新 | 臂A: 10 次采纳(reward=+2), 1 次差评(reward=-1) | 1. 检查 alpha/beta | alpha=1+10×2=21, beta=1+1=2（Spec §3.3 更新规则） | P2 |
| THO-05 | 奖励衰减 | 随时间推移调整权重 | 1. 跨时间窗口统计 | 近期反馈权重高于远期 | P3 |
| THO-06 | 离线任务调度 | 进程内调度器（§4.3） | 1. 检查定时任务执行 2. 验证结果 | 策略参数正确更新；进程未运行则下次启动补跑 | P2 |

### 2.12 质量评估 QualityEvaluation (Phase 3)

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| QAL-01 | 高相关性评分 | response 与 request 语义一致 | 1. evaluate_quality() | relevance >= 0.7（Spec §3.5 维度） | P2 |
| QAL-02 | 低相关性评分 | response 答非所问 | 1. evaluate_quality() | relevance <= 0.3 | P2 |
| QAL-03 | 可操作性：代码意图含可执行代码 | intent=code_gen 且含代码块 | 1. 检查 actionability | actionability 得分显著提升 | P2 |
| QAL-04 | 可操作性：含可执行步骤 | response 含分步骤操作指引 | 1. 检查 actionability | actionability 加分 | P2 |
| QAL-05 | 准确性：LLM-as-judge 事实一致 | response 事实正确 | 1. LLM-as-judge 检查 | accuracy 得分高 | P2 |
| QAL-06 | 角色权重：test 角色 accuracy 优先 | role=test | 1. 对比 default 权重计算 | 按 quality_rubric.yaml 中 test 权重（accuracy=0.4）加权 | P2 |

### 2.13 多模型与成本意识 (Phase 4)

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| MUL-01 | 模型适配器切换 | 注册 OpenAI+Anthropic | 1. 配置 intent=debug 用 Anthropic | 请求路由到 Anthropic | P3 |
| MUL-02 | Fallback 链 | 主模型全不可用 | 1. 主模型模拟失败 | 自动沿 fallback 链切换 | P3 |
| MUL-03 | 每日 token 上限 | 配置 daily_token_cap，当日用量已达上限 | 1. 发送请求 | 返回降级提示（含当日已用量）；preferences.force=true 可覆盖（§6.2） | P3 |
| MUL-04 | 用量 80% 提醒 | 当日用量达上限 80% | 1. 发送请求 | 响应 metadata.budget_warning 附带提示（§6.2） | P3 |
| MUL-05 | 成本核算 | 模型已定价 | 1. 完成请求 2. 查 interaction_logs | cost_usd = prompt/1e6×单价 + completion/1e6×单价；未定价模型为 NULL 且有告警日志（§6.1） | P2 |

### 2.14 Harness 自我改进 Harness-RSI (Phase 3)

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |
|----|------|----------|----------|----------|--------|
| HRSI-01 | 失败模式挖掘分桶 | 近 7 天有负信号（rejected/rating≤2/quality<0.5/fallback） | 1. 执行挖掘任务 | 按 intent×strategy 正确分桶；样本 < 5 的桶被忽略（§3.9） | P2 |
| HRSI-02 | 提案最小化约束 | 某失败桶样本充足 | 1. 生成提案 | 每桶 ≤ 3 个提案；每个提案仅触及单一槽位且绑定具体失败机制 | P2 |
| HRSI-03 | 回归门禁：通过 | 候选改动在回放集上指标提升且无退化 | 1. 执行回归验证 | 提案状态 → approved，regression_report 落库 | P2 |
| HRSI-04 | 回归门禁：否决 | 候选改动致某维度下降 > 5% | 1. 执行回归验证 | 提案状态 → rejected，不进入人工确认 | P2 |
| HRSI-05 | 人工确认晋升 | approved 提案 | 1. rsi_review approve | 状态 → active，槽位内容生效，payload 含 before/after 快照 | P2 |
| HRSI-06 | 观察期自动回滚 | active 提案在 7 天观察期内采纳率下降 > 5% | 1. 执行每日巡检 | 自动回滚到 before 快照，状态 → rolled_back | P2 |
| HRSI-07 | 手动回滚 | 任意已晋升提案 | 1. rsi_review rollback | 槽位内容恢复到指定版本 | P2 |
| HRSI-08 | 速率硬上限 | 单轮已生成 5 个提案 | 1. 继续生成 | 超出部分被截断；单槽位周变更 > 3 次被拒绝（§3.9 安全边界） | P1 |
| HRSI-09 | 元级修改禁止 | 提案内容涉及评估代码/门禁参数/提案机制自身 | 1. 提交此类提案 | 直接拒绝并记 warning（元级修改只能人工） | P1 |
| HRSI-10 | 事实日志不可改写 | 改进机制运行中 | 1. 检查 interaction_logs | 日志只被读取，无任何 UPDATE/DELETE（append-only） | P1 |
| HRSI-11 | 晋升即配置快照 | 提案被确认晋升 | 1. rsi_review approve 2. 查 config_snapshots | 生成新版本快照（version 单调递增），slots 含 6 槽位整体内容，trigger_proposal 指向该提案 | P2 |
| HRSI-12 | 回滚 = 切换快照 | 存在多个历史快照 | 1. rsi_review rollback 到指定 version | 目标快照标记 rolled_back_to 并以其内容重建槽位；原快照转 superseded；槽位内容与目标版本完全一致 | P2 |
| HRSI-13 | 快照合并语义 | 上一版本快照存在 | 1. 构造含缺省/null/有值字段的提案并晋升 | 缺省字段继承上一版本；null 字段重置回默认；有值字段覆盖（对象递归合并、数组整体替换） | P2 |
| HRSI-14 | 按需生成提案 | 近 7 天有负信号 | 1. rsi_review generate | 立即执行挖掘与提案生成，结果落库；单轮仍受 ≤ 5 个提案上限约束 | P2 |
| HRSI-15 | 快照导出 bundle | 存在 active 快照 | 1. rsi_review snapshot_export | 导出目录型 bundle（genome.json 清单 + 各槽位文件），可导入其他项目库 | P3 |
| HRSI-16 | 技能槽提案生效 | 提案新增技能（SKILL.md + trigger）并晋升 | 1. 触发匹配该 trigger 的查询 | 技能指令体注入提示词，来源标注 skill:<name>；技能文件进当次配置快照 | P2 |
| HRSI-17 | 技能热加载与停用 | 技能已在 config/skills/ | 1. 修改/删除技能文件（经提案流程）2. 再次查询 | 热加载生效无需重启；停用后不再注入 | P2 |


## 3. 压力测试方案

### 3.1 场景定义

> 个人本地工具量级：并发上限按 IDE 实际使用场景（同时数个请求）设计，不追求服务端 QPS 指标。

| 场景 | 并发数 | 持续时间 | 目标 | 通过标准 |
|------|--------|----------|------|----------|
| 基准测试 | 5 | 5min | 建立基线 | 成功率 100%（mock LLM） |
| 负载测试 | 20 | 10min | 验证本地并发处理 | P99 < 1s（不含模型调用） |
| 耐久测试 | 5 | 4h | 验证内存泄漏/句柄泄漏 | 内存增长 < 20%，无 OOM，SQLite 句柄无泄漏 |
| 模型 API 慢响应测试 | 10 | 10min | 高延迟模型下的并发管理 | asyncio 信号量限流生效，合理排队，不雪崩 |

### 3.2 压测工具与配置

- 工具：pytest-asyncio 自研压测脚本或 locust（stdio 直连）
- 指标采集：进程内统计 + 结构化日志分析
- 监控项：P50/P95/P99 延迟、错误率、进程内存/CPU、SQLite WAL 文件大小

### 3.3 预期瓶颈与监控

| 瓶颈 | 监控指标 | 调优策略 |
|------|----------|----------|
| SQLite 写锁竞争（WAL 单写者） | 写入延迟、SQLITE_BUSY 次数 | 写操作串行化队列、批量写入、异步落库 |
| OpenAI API 限流 | 429 错误率 | 客户端限速（asyncio.Semaphore）、退避重试 |
| Python GIL | CPU 使用率 | 异步 I/O 为主，CPU 密集（AST 扫描）放线程池 |
| 向量检索内存 | 进程 RSS | sqlite-vec 按需加载，嵌入缓存容量上限 |

---

## 4. 混沌测试方案

### 4.1 故障注入场景

| ID | 故障 | 注入方式 | 预期行为 | 验证点 |
|----|------|----------|----------|--------|
| CHA-01 | SQLite 不可用 | 数据目录设为只读 | sqlite 熔断器 OPEN，日志/画像读写跳过（内存态），请求仍可处理 | 主流程不受影响，记 error 日志（§5.4 Level 2） |
| CHA-02 | Embedding 服务不可用 | mock embedding 连续失败 | embedding 熔断器 OPEN，检索降级为纯 FTS5 | 知识检索仍可用，语义排序降级（§5.4 Level 1） |
| CHA-03 | OpenAI API 全部不可用 | 模拟所有模型返回 5xx | llm 熔断器 OPEN，fallback 无可用模型 | 返回友好静态降级消息，不崩溃（§5.4 Level 4） |
| CHA-04 | 网络延迟 2s | 注入 2s 延迟到 API 调用 | 超时控制触发，熔断器计数增加 | 不阻塞其他并发请求 |
| CHA-05 | 磁盘空间满 | 填充磁盘到 95% | 日志写入失败，请求处理不受影响 | 响应正常返回，记 error 日志 |
| CHA-06 | 反馈队列积压 | 生产 1 万条反馈消息 | asyncio.Queue 达 maxsize 后新反馈丢弃并记 warning，不 OOM | 消费速率平稳，内存可控 |
| CHA-07 | 时钟漂移 | 系统时间跳跃 5min | 熔断器计时使用单调时钟（`time.monotonic()`，Spec §5.1） | 熔断器状态不异常 |
| CHA-08 | SQLite 文件损坏 | 写入损坏字节后重启 | 启动 `PRAGMA integrity_check` 自检发现损坏，自动备份损坏文件并重建空库 | 进程可启动，知识可经 bootstrap 重建 |

### 4.2 恢复验证

| ID | 场景 | 恢复步骤 | 验证点 |
|----|------|----------|--------|
| REC-01 | SQLite 恢复 | 恢复数据目录可写 | 熔断器 HALF_OPEN→CLOSED，日志写入恢复正常 |
| REC-02 | Embedding 恢复 | embedding 服务恢复 | 向量检索自动恢复，混合检索重新生效 |
| REC-03 | OpenAI API 恢复 | API 恢复正常 | 熔断器 HALF_OPEN -> CLOSED，正常调用恢复 |
| REC-04 | 全部恢复 | 所有依赖正常 | 全链路验证，确认回到 Level 0 |

---

## 5. 数据一致性测试

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 |
|----|------|----------|----------|----------|
| CON-01 | 模型调用后进程崩溃 | 两段式日志（Spec §4.1）：pending 记录已在模型调用前 INSERT | 1. pending 已写入 2. 模拟崩溃 3. 重启后客户端持 token 提交反馈 | token 可关联到唯一 pending 记录，反馈正常受理；离线对账任务后续补齐终态 |
| CON-02 | 日志 INSERT pending 后崩溃 | 崩溃发生在 INSERT 后 UPDATE 前 | 1. INSERT pending 2. 进程崩溃 | 重启后离线对账任务（§4.3，每小时）扫描 pending 日志，补齐 UPDATE 或标记异常 |
| CON-03 | 反馈重复提交幂等 | 同一 feedback_token 提交两次 | 1. 第一次处理成功 2. 第二次提交 | 第二次返回已处理，策略统计不重复 |
| CON-04 | 队列消费异常 | 反馈处理 task 消费时抛异常 | 1. 注入一条毒消息 | 该条标记失败并跳过，后续消息正常消费，队列不阻塞 |
| CON-05 | 配置更新正在生效时请求 | 更新策略时发送请求 | 1. 更新策略 2. 同时发送请求 | 读旧配置或新配置，不出现配置混合 |
| CON-06 | 迁移中断 | 迁移脚本执行到一半 kill 进程 | 1. 中断迁移 2. 重启 | user_version 未前进，重启后重入执行幂等脚本，库结构正确（§2.2） |

---

## 6. 安全测试

| ID | 场景 | 测试方法 | 预期结果 | 优先级 |
|----|------|----------|----------|--------|
| SEC-01 | SQL 注入 | raw_input 含 SQL 注入代码 | 参数化查询，SQL 注入不生效 | P0 |
| SEC-02 | XSS 注入 | response 含 <script> 标签 | 响应中 HTML 转义 | P1 |
| SEC-03 | feedback_token 伪造 | 尝试构造/猜测 token | 校验失败，返回 token 无效错误 | P1 |
| SEC-04 | 敏感信息日志泄露 | 检查日志与 rsi.db | API Key、密码等已脱敏（§8.1） | P0 |
| SEC-05 | 知识条目注入 | 批量提交大量/超大条目 | 大小限制生效，单条内容长度校验拒绝 | P2 |
| SEC-06 | 项目配置明文 Key | rsi-boot.yaml 中写入 `sk-...` 明文 | 配置加载器拒绝启动并提示走环境变量（§8.2） | P0 |
| SEC-07 | 外发 LLM 内容扫描 | prompt 中含密钥/内网地址 | 外发前已脱敏（§8.1 双时机） | P0 |

---

## 7. 升级测试（PRAGMA user_version 迁移）

| ID | 场景 | 前置条件 | 测试步骤 | 预期结果 |
|----|------|----------|----------|----------|
| MIG-01 | 全新安装 | 无 .rsi/ 目录 | 1. 启动进程 | 自动建库，所有表创建成功，user_version = 最新 |
| MIG-02 | 增量升级 | 已有 v1 schema | 1. 放入 002_xxx.sql 2. 启动 | 新字段/表创建，旧数据不丢失，user_version 前进 |
| MIG-03 | 中断重入 | 迁移执行中 kill | 1. 中断 2. 重启 | 事务回滚或幂等重跑，库结构最终正确 |
| MIG-04 | 迁移前后数据兼容性 | 升级前有 100 条日志 | 1. 升级 2. 查询日志 | 旧数据可正常读取，新字段为默认值 |
| MIG-05 | 幂等重复执行 | 已是最新版本 | 1. 再次启动 | 迁移跳过，无重复执行副作用 |

---

## 8. 测试环境要求

| 环境 | 用途 | 配置 |
|------|------|------|
| 单元测试 | 模块级快速验证 | pytest，mock 外部依赖（LLM/Embedding） |
| 集成测试 | 模块间交互验证 | pytest + tmp_path 临时目录 SQLite（含 sqlite-vec 扩展加载） |
| E2E 测试 | 完整链路 | stdio 子进程启动真实 Server + mock LLM |
| 性能测试 | 压测 | 本机即可（个人工具量级） |
| 混沌测试 | 容错验证 | mock 故障注入（只读目录/损坏文件/慢响应） |

---

## 9. CI/CD 集成

| 阶段 | 触发条件 | 运行测试 | 门禁标准 |
|------|----------|----------|----------|
| Pre-commit | 每次提交 | 单元测试 + lint | 全部通过 |
| PR | 创建 PR | 单元 + 集成 | 整体行覆盖率 > 80%（PR 门禁口径） |
| Daily | 每日凌晨 | E2E + 契约 | 全部通过 |
| Weekly | 每周日 | 性能 + 安全 | P99 < 目标值 |
| Release | 发布前 | 全部测试 | 全部通过 |

---

## 10. 测试数据准备

| 数据 | 用途 | 准备方式 |
|------|------|----------|
| 意图识别测试集 | 验证各意图分类 | 50 条/意图，含边界情况 |
| 知识检索测试集 | 验证混合检索准确率 | 1000 条知识条目 + 100 条查询 |
| 用户画像测试数据 | 验证项目间画像隔离与全局画像共享 | 5 个项目 x 全局/项目双层画像 |
| 反馈模拟数据 | 验证学习闭环 | 1000 条反馈记录（含各 action） |
| 压测请求数据 | 性能测试 | 10 种典型请求模板 |

---

## 11. 测试通过标准

| 维度 | 标准 |
|------|------|
| 功能测试 | 所有 P0 用例 100% 通过，P1 > 90% |
| 代码覆盖率 | 核心模块单元测试行覆盖率 ≥ 85%；PR 门禁整体行覆盖率 > 80%；分支覆盖率 > 75%（口径说明：85% 为核心模块目标，80% 为全仓门禁线） |
| 性能测试 | P99 < 1s（不含模型调用）；冷启动 < 3s |
| 混沌测试 | 单项故障 5min 内自愈或降级正常 |
| 安全测试 | 无高危漏洞，中危 < 3 个 |
| 升级测试 | 迁移幂等可重入，数据不丢失 |

---

## 12. 算法质量评估与验收基线（RSI 特有）

> 传统软件测试（单元/集成/契约）验证「代码正确性」，本节验证「算法有效性」——RSI 特有组件必须有**量化基线 + 版本化评测集 + 统计方法**，不接受「跑通即验收」。评测代码位于 `tests/eval/`，数据集位于 `tests/eval/datasets/`（JSONL + 标注规范 README，随仓库版本化），报告归档 `tests/eval/reports/`。

### 12.1 意图分类器

| 项 | 基线 |
|----|------|
| 评测集 | 每意图 ≥ 50 条标注样本（模板生成 + 人工标注 + 脱敏日志回流三来源），通用意图与角色意图分开统计 |
| 验收指标 | 规则版（M1）Top-1 准确率 ≥ 85%；LLM 版（P2.2）Top-1 ≥ 92% 且宏 F1 ≥ 0.85 |
| 回归门槛 | 任何变更后重跑评测集，指标不得低于上一基线 2pp；混淆矩阵归档可查 |

### 12.2 知识检索（混合检索调优）

| 项 | 基线 |
|----|------|
| 评测集 | 每项目类型 ≥ 30 条 query→相关条目标注对，覆盖 ≥ 3 类项目（Web 后端 / 前端 / 脚本工具） |
| 验收指标 | Recall@5 ≥ 80%（最差项目类型 ≥ 70%）；MRR ≥ 0.65 |
| 参数调优 | BM25 k1/b、RRF k、TopK 经网格搜索在评测集上选定，选定值与得分记录归档；阈值 cosine 0.6 的 precision/recall 权衡表归档 |
| 回归门槛 | 参数或检索逻辑变更必须重跑全量评测集，任何项目类型退化 > 2pp 即否决 |

### 12.3 Thompson Sampling 收敛

| 项 | 基线 |
|----|------|
| 方法 | 统计仿真（非确定性断言）：合成 Bernoulli 臂环境，1000 轮 × 100 次独立重复 |
| 验收指标 | 1000 轮时最优臂选择概率 ≥ 90%；累积 regret 较均匀随机基线低 ≥ 40% |
| 正确性 | Beta 后验参数更新规则用确定性单测覆盖（alpha/beta 精确断言） |
| 真实环境 | 影子模式（shadow）观测采样分布，不做硬断言；异常漂移（单臂占比 > 95% 且非最优）告警 |

### 12.4 Harness 提案质量与门禁有效性

| 项 | 基线 |
|----|------|
| 「最小化」客观标准 | 单槽位 + 单目标 + diff ≤ 50 行 + 绑定 ≥ 1 个失败桶 ID + payload 含 before/after——五项缺一即非最小化，直接 rejected |
| 门禁有效性 | 注入 ≥ 10 个已知坏改动（种子退化集），回归门禁拦截率必须 100% |
| 提案生成质量 | 首批 ≥ 20 个提案的人工接受率 ≥ 50%；低于则判定挖掘/生成质量不达标，调优提案 prompt 后重测 |
| 运行监测 | 观察期回滚率 > 30% 触发提案策略人工审查（§3.9 安全边界之外的质量信号） |

### 12.5 画像推断质量

| 项 | 基线 |
|----|------|
| 推断准确率 | 规则推断结果抽样人工评估（每维度 ≥ 30 样本），准确率 ≥ 80% |
| 衰减稳健性 | 半衰期参数 ±50% 扰动下画像结论稳定性分析归档（防参数敏感） |

### 12.6 评测基础设施与 CI 分级

| 级别 | 内容 | 时机 |
|------|------|------|
| 快速门槛 | 意图分类评测 + 门禁注入验证（< 5 min） | 每次 PR |
| 全量评测 | 检索网格复测 + Thompson 仿真 + 画像抽样 | nightly |
| 基线管理 | 基线数值存 `tests/eval/baselines.json`，版本化；提升基线需 PR 说明依据，禁止悄降 |

## 13. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| sqlite-vec 平台兼容性（Windows/macOS/Linux  wheel） | 中 | 中 | CI 三平台矩阵测试；缺失时 brute 后端回退（KNO-09~11），FAISS 为可选未来后端 |
| OpenAI API 费用失控 | 中 | 高 | E2E 测试使用 mock 模型，不调用真实 API |
| Thompson Sampling 难以测试 | 中 | 中 | 用统计仿真验证，非确定性断言 |
| 日志表数据膨胀影响测试速度 | 高 | 低 | 每个测试独立临时库文件，测试后清理 |
