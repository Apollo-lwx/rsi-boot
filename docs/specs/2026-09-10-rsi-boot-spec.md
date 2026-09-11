# RSI Boot — 技术规格文档 (Spec)

> 版本：v2.6 | 日期：2026-09-10 | 状态：正式版（评审修复：+ M1 轻量价值闭环）

---

## 修订记录

| 版本 | 日期 | 修订人 | 变更内容 |
|------|------|--------|----------|
| v2.6 | 2026-09-10 | PO (评审修复) | 响应外部评审 #1/#2：M1 新增轻量价值闭环（Plans P1.18）——§3.2/§4.2 新增分阶段可用性说明（M1 FTS5-only 检索 + rsi_feedback 基础版，Phase 2/3 在原路径深化）；数据自 M1 起积累，消除 P3.6 的数据空窗 |
| v2.5 | 2026-09-10 | PO (评审修复) | 响应外部评审 #5：§9.1 新增算法验收基线原则——RSI 特有算法组件（意图分类/混合检索/Thompson/提案门禁/画像推断）须通过量化基线回归门槛（权威数值在测试计划 §12），覆盖率门禁不代表算法有效性 |
| v2.4 | 2026-09-10 | PO (评审修复) | 响应外部评审 #3/#4：§2.3 新增「向量后端抽象与回退」——VectorBackend 协议（sqlite-vec 首选 / brute numpy 暴力余弦内置回退 / FAISS 可选未来），embedding BLOB 落 knowledge_items 作为事实来源、vec0 降为可重建加速索引（回退零迁移），配置 vector.backend auto 探测，降级链 sqlite-vec→brute→纯 FTS5；§2.2 knowledge_items 新增 embedding BLOB 列；bootstrap scanner 分层为核心/深度两期（工作量重估见 Plans P1.17/P2.6） |
| v2.3 | 2026-09-10 | PO (Skill 槽补全) | 修正 v2.2「Skill/Tools/MCP 三槽不适用」的论断（Superpowers 证明 MCP 形态可有数据驱动技能层）：可写面 5→6 槽，新增 skill 槽（`config/skills/<name>/SKILL.md` 文件型技能，frontmatter 声明 trigger，策略引擎匹配注入，与知识注入共用预算与标注）；§3.9 新增三槽修正说明（Skill 纳入、声明式 Tools 记为未来路径、MCP 槽为范围取舍而非形态必然）；§2.2 提案 slot 枚举与快照 slots 注释同步；§11 新增 config/skills/ 目录 |
| v2.2 | 2026-09-10 | PO (Genome 借鉴) | 对照 MetaRSI-v1 / RSI-Harness 开源实现做架构对齐：§3.9 新增与 MetaRSI 五槽位的映射表及 Skill/Tools/MCP 不适用的说明；版本化与回滚升级为 Genome 式整体快照（§2.2 新增 config_snapshots 表，晋升即快照、回滚 = 切换快照、缺省继承/null 重置/有值覆盖的合并语义、快照可导出为目录型 bundle）；rsi_review 新增 generate 按需提案生成与 snapshot_list/switch/export 操作（§7.1）；安全边界新增「与 MetaRSI-v1 的刻意取舍」段（RSI² 纵向优化与 Model-RSI 刻意不做及未来路径） |
| v2.1 | 2026-09-10 | PO (Harness-RSI) | 引入 Harness-RSI 自我改进机制（溯源 MetaRSI-v1 / Self-Harness / AHE，2026）：新增 §3.9（5 槽位可写面模型、提案生命周期状态机、失败模式挖掘 → 最小化提案 → 回放集回归门禁 → 人工确认 → 7 天观察期自动回滚、防递归失控安全边界）；§2.2 新增 harness_proposals 表；§4.3 新增失败模式挖掘/提案生成验证/观察期巡检三个离线任务；§7.1 新增 rsi_review（P2）与 rsi_stats（P3）工具登记；§11 新增 learning/ 模块目录 |
| v2.0 | 2026-09-10 | PO (定位重构) | **产品定位转向：从「团队 AI 网关」重构为「个人本地 MCP 工具」（Superpowers 同类，安装即用、零运维）**。移除：HTTP/WS 共享模式、认证鉴权（API Key/JWT/锁定）、权限角色体系、限流（令牌桶）、多租户隔离、PostgreSQL/Qdrant/Redis/Alembic/docker 部署面、/admin 与 /auth 端点。替换：存储栈改为 SQLite 单文件（WAL）+ sqlite-vec 向量 + FTS5 全文（§2.2/§2.3）；Redis 数据结构改为进程内 TTLCache/asyncio.Queue/内存熔断器（§2.4）；配置链简化为 默认→~/.rsi→项目 yaml→请求参数（§2.5）；熔断/重试/降级全面改写为 SQLite+LLM 依赖面（§5）；多租户章改为成本控制章，预算简化为可选每日 token 上限（§6）；§7.2 改为身份与信任模型（git user.email）；安全章收敛为脱敏/凭证管理/数据生命周期/HTTPS 外发（§8）；集成方案仅 stdio 本地模式，多项目=每项目独立 .rsi/ + ~/.rsi/global.db 双层（§10）；目录结构同步（§11）。保留：Pipeline 编排、意图/策略/Thompson、画像学习、bootstrap 自学习、脱敏、成本统计等核心智能能力 |
| v1.4 | 2026-09-10 | PO (实现细节深化) | 认证鉴权落到算法级：API Key 生成/校验完整流程（256 bit 随机主体 + 前缀索引 + SHA-256 + 常量时间比对）、JWT claims/签名密钥/吊销列表、失败锁定 Redis 实现；新增 api_keys 表；限流明确为令牌桶（否决滑动窗口）并给出参数表与 Redis Lua 原子脚本、429 Retry-After 公式、进程内降级语义；脱敏补 10 条正则模式表（§10.9.7 复用同源）；AES-256-GCM 加密细节（nonce 管理/密文格式/密钥轮换流程）；熔断器补失败计入口径与状态机精确定义（HALF_OPEN 单探测、fail-fast 异常类型、状态存储格式）；重试补 full jitter 公式与可重试/不可重试错误分类、流式幂等约束；降级链补各级触发条件与自动恢复；成本核算补 token 计量来源（usage 字段/tiktoken 估算）与公式、未定价模型处理；预算控制补 Redis 计数与预估检查时机；§7.1 补工具 inputSchema 契约 |
| v1.3 | 2026-09-10 | PO (实现细节补全) | 新增 §3.8 用户画像构建与更新（存储/缓存映射、初始构建推理规则与阈值、运行时增量更新、30 天半衰期衰减、离线聚合算法、参数速查表）；§3.1 补 Phase 2 few-shot 实现细节（提示词结构/输出契约/置信度门槛/降级链/缓存）；§3.2 补检索参数表（BM25 k1/b、TopK=20、TOP_N=5、归一化方法、过滤隔离、索引维护）；§3.3 补 Thompson 状态持久化与离线衰减/淘汰规则；§3.5 补三维度具体计算方法与采样策略；§4.2 补反馈字段级更新规则映射表（7 种 action 全覆盖）；§4.3 展开画像更新/策略调优/BM25 重建/知识提取任务细节；strategy_configs 表新增 alpha/beta/exposure_count 列（修复 Thompson 状态无持久化载体的缺口） |
| v1.2 | 2026-09-10 | PO (二次审阅修复) | §10.9 项目自学习整块移至 §11 目录结构之前（修复 v1.1 插入位置错误导致的章节错位）；§10.9.13 显式反馈学习命名统一为 rsi_feedback |
| v1.1 | 2026-09-10 | PO (审阅修复) | 修复审阅发现的阻塞级问题：RRF 注入门槛改为原始分数（cosine >= 0.6）；分区表主键/唯一约束包含分区键并补分区预建；熔断器 Redis 断连改为仅 Redis 自身 OPEN、其余降级进程内状态；统一配置继承链（§2.5 = §10.3.3）；strategy_configs 增加 role 列、Qdrant payload 增加 roles/domain；权限角色（admin/member/viewer）与业务角色正交化；role 共享模式下服务端解析防伪造；日志两段式写入与 token 时序前置；API Key 改 SHA-256+前缀索引、补 JWT 签发端点与失败锁定；代码片段落库前密钥扫描；加密字段清单与密钥轮换；日志归档统一 90 天；补限流桶容量、上下文截断、diff>20% 知识提取阈值；ContentItem/UserProfile/UserPreferences 模型补全；§11 补 scanner/cli/tools/roles 目录；修复 datetime.utcnow 弃用、Let's Encrypt 引号等 |
| v1.0 | 2026-09-10 | PO (版本对齐) | 统一版本号为 v1.0；修复重复标题；PRD/Specs/Plans 文档版本对齐；新增可追溯矩阵 |
| v0.5 | 2026-09-10 | PO (终审) | 新增角色权重质量评分（3.5）；新增响应格式化角色感知渲染（3.7）；ContentItem 新增 applicable_roles 字段；角色质量权重可配置；格式化规则可配置
| v0.4 | 2026-09-10 | PO (评审汇总) | 编排器改为 Pipeline 模式；新增 DataServiceProxy；Feedback Token 时序修复；独立 Worker 架构桩；Redis 断连默认 OPEN；引入 Alembic；ContextSchema 约束；MCP initialize/tools/list 支持；stdio 传输优先；Thompson Sampling 详细设计标记；DDL 补全；TypedDict 替代 Dict[str,Any]；config_merger 模块；认证鉴权；数据脱敏；限流算法；数据生命周期；流式响应 SSE |
| v0.2 | 2026-09-10 | 初始 | 初稿 |

---

## 1. 系统架构

### 1.1 整体架构概览

RSI Boot 是**本地单用户 MCP 工具**（定位对齐 Superpowers 类插件：安装即用、零外部服务依赖）。以 MCP stdio Server 形态运行，由 IDE 按项目启动（cwd = 项目根目录），全部数据存储在项目 `.rsi/` 目录的**单文件 SQLite** 中。无认证、无多租户、无限流——进程即用户，本地环境即信任边界。

分层架构从上到下：MCP 协议层、核心编排层、模型适配层、数据层（SQLite 单文件 + 进程内结构）和学习层。

```
┌─────────────────────────────────────────────────────────────┐
│                MCP 客户端 (IDE / CLI，本地进程)               │
│              (MCP Protocol / JSON-RPC 2.0 over stdio)        │
└───────────────────────────┬─────────────────────────────────┘
                            │ stdio（本地进程间，无认证）
┌───────────────────────────▼─────────────────────────────────┐
│                     RSI Boot (MCP Server)                     │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │  预处理模块     │  │  策略引擎       │  │  后处理模块    │  │
│  │  - 意图识别    │  │  - 路由决策     │  │  - 结果解析    │  │
│  │  - 上下文组装  │  │  - 提示词渲染   │  │  - 格式化      │  │
│  │  - 知识检索(P2)│  │  - 模型选择     │  │  - 质量评估(P3)│  │
│  └──────┬────────┘  └──────┬─────────┘  └───────┬─────────┘  │
│         │                  │                     │            │
│  ┌──────▼──────────────────▼─────────────────────▼────────┐ │
│  │               编排器 (Orchestrator)                      │ │
│  │   - Pipeline 流程编排   - 错误处理/重试  - 进程内熔断器  │ │
│  │   - 超时控制   - 个人用量统计                            │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                               │
│  ┌───────────────────────────▼───────────────────────────┐  │
│  │              模型适配层 (Model Adapter)                │  │
│  │   OpenAI | (Phase 4: Anthropic / DeepSeek / ...)      │  │
│  │   统一接口: generate(prompt, params) -> ModelResponse   │  │
│  └───────────────────────────┬───────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────┘
                               │ HTTPS（API Key 走环境变量）
                     ┌─────────▼─────────┐
                     │   外部 LLM 服务    │
                     └───────────────────┘

┌─────────────────────────────────────────────────────────────┐
│         数据层（项目 .rsi/ 目录，单文件零依赖）                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ SQLite (rsi.db, WAL 模式)                             │  │
│  │  - 交互日志 / 反馈      - 用户画像      - 策略配置     │  │
│  │  - 知识条目 + sqlite-vec 向量 + FTS5 全文（同库同事务）│  │
│  ├──────────────────────────────────────────────────────┤  │
│  │ 进程内结构（替代 Redis，随进程生命周期）               │  │
│  │  - TTLCache 响应/画像缓存  - asyncio.Queue 反馈队列   │  │
│  │  - 内存熔断器状态         - 内存用量计数               │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   学习层 (Learning Layer) Phase 3+            │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ 反馈处理器        │  │ 知识提取器        │                 │
│  │ - Feedback 消费  │  │ - 高质量交互筛选   │                 │
│  │ - 隐式信号解析    │  │ - 条目去重        │                 │
│  │ - 画像更新        │  │ - 人工确认队列    │                 │
│  └────────┬─────────┘  └────────┬─────────┘                 │
│           │                     │                           │
│  ┌────────▼─────────────────────▼─────────┐                 │
│  │        策略优化器                         │                 │
│  │  - Thompson Sampling A/B 测试            │                 │
│  │  - 采纳率统计                            │                 │
│  │  - 策略权重自动调整                       │                 │
│  └──────────────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

**关键架构决策（v2.0 定位修正）**：

| 决策 | 结论 | 理由 |
|------|------|------|
| 运行形态 | 仅 stdio 本地模式 | 工具/插件定位，安装即用；不提供 HTTP 共享服务 |
| 存储 | SQLite 单文件（WAL）+ sqlite-vec + FTS5 | 零外部服务；向量/全文/关系数据同库同事务，无双写一致性问题 |
| 缓存/队列/熔断状态 | 进程内（TTLCache / asyncio.Queue / 内存 dict） | 单用户单进程，无需 Redis |
| 认证/多租户/限流 | 无 | 本地进程即信任边界；单用户无需限流（用量自我约束走 §6 预算） |
| 用户标识 | `user_id` 默认取 `git config user.email`，缺失时为 `local` | 画像只服务本人，标识仅用于数据归集 |

---


## 2. 数据结构设计

### 2.1 核心 Pydantic 模型

#### RSIRequest（请求输入）

```python
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from uuid import UUID, uuid4
from datetime import datetime, timezone

def utcnow() -> datetime:
    """datetime.utcnow 已在 Python 3.12+ 弃用，统一使用带时区的当前时间"""
    return datetime.now(timezone.utc)

class ContextInfo(BaseModel):
    """上下文信息，TypedDict 风格约束"""
    language: Optional[str] = None
    file_path: Optional[str] = None
    selection: Optional[str] = None
    project_root: Optional[str] = None
    chat_history: Optional[List[Dict[str, str]]] = None

class UserPreferences(BaseModel):
    detail_level: str = "normal"
    code_style: str = "default"
    response_lang: str = "zh"
    # 角色特定偏好的扩展命名空间（如 test_framework / doc_template / report_format），
    # 键值由角色配置（config/roles/*.yaml）定义，核心模型不硬编码角色字段
    role_specific: Dict[str, Any] = Field(default_factory=dict)

class RequestMetadata(BaseModel):
    client_type: Optional[str] = None
    client_version: Optional[str] = None
    timestamp: Optional[datetime] = None

class RSIRequest(BaseModel):
    request_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(..., pattern=r"^[a-zA-Z0-9_@.-]{1,128}$")
    project_id: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    session_id: Optional[str] = None
    role: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9_-]{0,49}$")
    # role: 业务角色标识（如 developer/test/pm），仅影响意图路由/知识检索/响应格式化，
    # 不含任何权限语义（本地单用户工具无权限体系）。
    # 信任客户端传入；为 None 时走通用模式，系统不预设用户类型。
    raw_input: str = Field(..., min_length=1, max_length=32768)
    intent: Optional[str] = None
    context: ContextInfo = Field(default_factory=ContextInfo)
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)
```

#### RSIResponse（响应输出）

```python
from enum import Enum

class ContentType(str, Enum):
    TEXT = "text"
    CODE = "code"
    MARKDOWN = "markdown"
    JSON = "json"
    IMAGE = "image"
    ERROR = "error"

class ContentItem(BaseModel):
    type: ContentType
    body: str = Field(..., max_length=65536)
    language: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    order: int = 0
    # 该内容块适用的业务角色列表，None 表示通用；Postprocessor 按角色过滤（见 §3.7）
    applicable_roles: Optional[List[str]] = None

class Suggestion(BaseModel):
    type: str = Field(..., pattern=r"^(alternative_code|related_doc|follow_up|optimization)$")
    body: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CostInfo(BaseModel):
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Field(default=Decimal("0"), max_digits=10, decimal_places=6)
    latency_ms: int = 0

class RSIResponse(BaseModel):
    request_id: UUID
    api_version: str = "1.0"
    status: str = Field(..., pattern=r"^(success|error|partial)$")
    content: List[ContentItem] = Field(..., min_length=1)
    suggestions: List[Suggestion] = Field(default_factory=list)
    cost_info: Optional[CostInfo] = None
    quality_score: Optional[float] = Field(None, ge=0, le=1)
    feedback_token: str
    error: Optional[Dict[str, Any]] = None
```

#### Feedback（用户反馈）

```python
class FeedbackAction(str, Enum):
    ACCEPTED = "accepted"        # 完全采纳
    MODIFIED = "modified"        # 修改后采纳
    IGNORED = "ignored"          # 忽略
    REJECTED = "rejected"        # 明确拒绝
    COPIED = "copied"            # 复制了内容
    APPLIED = "applied"          # 应用了建议
    REFERENCED = "referenced"    # 参考了但未直接使用（测试/PM 场景常见）

class Feedback(BaseModel):
    feedback_token: str
    user_id: str
    project_id: Optional[str] = None
    action: FeedbackAction
    modified_content: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2048)
    timestamp: datetime = Field(default_factory=utcnow)
```

#### ProcessedRequest（预处理输出 — 独立 Pydantic 模型）

```python
class ProcessedRequest(BaseModel):
    """完全解析后的内部中间表达，与原始 RSIRequest 形成清晰区分"""
    request: RSIRequest
    intent: str
    confidence: float = Field(..., ge=0, le=1)
    retrieved_docs: List["KnowledgeItem"] = Field(default_factory=list)
    user_profile: Optional["UserProfile"] = None
    merged_config: Dict[str, Any] = Field(default_factory=dict)
    additional_context: Dict[str, Any] = Field(default_factory=dict)
```

#### KnowledgeItem（知识条目）

```python
class KnowledgeItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: str
    title: str
    content: str
    content_type: str = "documentation"
    tags: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None
    source_url: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    status: str = "active"
```

#### UserProfile（用户画像）

```python
class UserProfile(BaseModel):
    user_id: str
    project_id: Optional[str] = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    frequently_used_intents: Dict[str, int] = Field(default_factory=dict)
    preferred_models: List[str] = Field(default_factory=list)
    feedback_history: List[str] = Field(default_factory=list)
    knowledge_interests: List[str] = Field(default_factory=list)
    # 以下字段由 rsi bootstrap 初始填充（见 §10.9.4）
    expertise: List[str] = Field(default_factory=list)              # 专长领域（文件归属 + 代码结构推断）
    project_insights: Dict[str, Any] = Field(default_factory=dict)  # 项目级综合认知（架构/构建/CI/文档质量等）
    history_summary: Optional[str] = None                           # 项目历史摘要
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
```

#### feedback_token 生成机制

```python
import hmac
import hashlib
import uuid

def generate_feedback_token(request_id: UUID, user_id: str, secret_key: str) -> str:
    """
    生成 feedback_token：UUIDv4 + HMAC-SHA256 签名
    - 前半段：UUIDv4（唯一标识）
    - 后半段：HMAC(request_id + user_id, secret_key) 前 16 位
    - 用途：防止伪造，同时可校验 token 是否属于该请求
    - 时序：在请求入口（PreprocessHandler）生成；Log Handler 先 INSERT status='pending'
           的日志记录（含 request_id 与 token），再进入模型调用，响应完成后 UPDATE 终态。
           token 跟随 RSIResponse 返回；客户端在 Feedback 中原样提交，
           服务端按 token 索引回查日志并校验 HMAC 匹配后才更新记录
    """
    raw = f"{request_id}:{user_id}"
    signature = hmac.new(
        secret_key.encode(),
        raw.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    token_uuid = str(uuid.uuid4())
    return f"{token_uuid}-{signature}"
```

---
### 2.2 数据库表设计 (SQLite)

单文件库：项目根目录 `.rsi/rsi.db`，WAL 模式（读不阻塞写）。数据落点分两层（§10.4）：`<project>/.rsi/rsi.db` 存项目域数据（本节全部表）；`~/.rsi/global.db` 存全局画像（`user_profiles` 中 `project_id=''` 的行）与跨项目用量统计，两库表结构同构。SQLite 无分区/数组/JSONB 类型，对应调整：数组存 JSON 文本、JSONB 存 TEXT(JSON)、时间戳存 ISO8601 UTC TEXT、UUID 存 TEXT hex。个人工具数据量级（年约数万行日志）无需分区，归档走 §8.3 导出删除。

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS interaction_logs (
    id                TEXT PRIMARY KEY,          -- UUIDv4 hex
    request_id        TEXT NOT NULL UNIQUE,
    user_id           TEXT NOT NULL,
    project_id        TEXT,                      -- 项目标识（§10.3.2 自动发现），非租户概念
    session_id        TEXT,
    raw_input         TEXT NOT NULL,
    context           TEXT,                      -- JSON
    intent            TEXT,
    intent_confidence REAL,
    strategy_name     TEXT,
    model_name        TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    cost_usd          REAL,                      -- 未定价模型为 NULL（§6.1）
    latency_ms        INTEGER NOT NULL,
    quality_score     REAL,
    status            TEXT NOT NULL DEFAULT 'pending',   -- pending/success/error
    feedback_token    TEXT NOT NULL UNIQUE,
    feedback_action   TEXT,
    feedback_rating   INTEGER CHECK (feedback_rating BETWEEN 1 AND 5),
    created_at        TEXT NOT NULL,             -- ISO8601 UTC
    completed_at      TEXT
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      TEXT NOT NULL,
    project_id   TEXT NOT NULL DEFAULT '',       -- '' = 跨项目全局画像
    profile_data TEXT NOT NULL DEFAULT '{}',     -- JSON（UserProfile 序列化）
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

CREATE TABLE IF NOT EXISTS project_configs (
    project_id  TEXT PRIMARY KEY,
    config_data TEXT NOT NULL DEFAULT '{}',      -- JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    intent         TEXT NOT NULL,
    role           TEXT,                         -- 适用业务角色，NULL 表示通用
    strategy_name  TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    template_ref   TEXT,
    weight         REAL DEFAULT 1.0,
    alpha          REAL NOT NULL DEFAULT 1.0,    -- Thompson 成功计数（§3.3）
    beta           REAL NOT NULL DEFAULT 1.0,    -- Thompson 失败计数（§3.3）
    exposure_count INTEGER NOT NULL DEFAULT 0,   -- 曝光次数（采纳率统计与最小曝光门槛用）
    is_active      INTEGER NOT NULL DEFAULT 1,   -- SQLite 无 BOOLEAN，0/1
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_type TEXT DEFAULT 'documentation',
    roles        TEXT DEFAULT '[]',              -- JSON 数组，空数组表示通用
    domain       TEXT,                           -- 领域标签：test_framework/code_style/prd_template
    tags         TEXT DEFAULT '[]',              -- JSON 数组
    source_url   TEXT,
    status       TEXT DEFAULT 'active',          -- active/pending_review/rejected/archived
    embedding    BLOB,                           -- float32 LE 字节，向量后端的事实来源（§2.3 回退设计）
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Harness 自我改进提案（§3.9，全程落库可审计）
CREATE TABLE IF NOT EXISTS harness_proposals (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    slot              TEXT NOT NULL,             -- prompt_template/skill/strategy/intent_rule/knowledge/profile
    target_ref        TEXT NOT NULL,             -- 槽位目标标识（模板名/策略 id/规则名/条目 id）
    action            TEXT NOT NULL,             -- add/remove/modify
    payload           TEXT NOT NULL,             -- JSON：before/after 快照或 diff
    evidence          TEXT,                      -- JSON：失败模式桶与样本 request_id 列表
    regression_report TEXT,                      -- JSON：回放集指标对比
    status            TEXT NOT NULL DEFAULT 'proposed',  -- proposed/validating/approved/rejected/active/rolled_back
    created_at        TEXT NOT NULL,
    decided_at        TEXT,                      -- 人工确认时间
    activated_at      TEXT,
    observe_until     TEXT                       -- 观察期截止（晋升后 7 天）
);

-- 配置版本快照（§3.9 Genome 式整体快照，晋升即产生新版本）
CREATE TABLE IF NOT EXISTS config_snapshots (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    version           INTEGER NOT NULL,          -- 项目内单调递增
    trigger_proposal  TEXT,                      -- 触发生成的 proposal id（手动快照为 NULL）
    slots             TEXT NOT NULL,             -- JSON：6 槽位整体导出（模板/技能/策略/规则/知识引用/画像引用）
    status            TEXT NOT NULL DEFAULT 'active',  -- active/superseded/rolled_back_to
    created_at        TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE INDEX IF NOT EXISTS idx_logs_created_at ON interaction_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_intent ON interaction_logs(intent);
CREATE INDEX IF NOT EXISTS idx_logs_status ON interaction_logs(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge_items(project_id, status);
CREATE INDEX IF NOT EXISTS idx_strategy_lookup ON strategy_configs(project_id, intent, role);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON harness_proposals(status, project_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON config_snapshots(project_id, version DESC);
```

#### Schema 迁移策略（PRAGMA user_version）

- 版本号存 `PRAGMA user_version`；`migrations/` 目录下 `001_init.sql`、`002_xxx.sql` 按序执行
- 启动时检查 `user_version`，落后则依次执行迁移脚本，每个脚本以事务包裹（SQLite DDL 支持事务）
- 每个迁移脚本必须幂等（`IF NOT EXISTS` / 版本判断），支持中断后重入
- 不引入 Alembic——SQLite 场景下 PRAGMA + 顺序 SQL 脚本足够，减少依赖

### 2.3 向量检索 Schema (sqlite-vec + FTS5)

Phase 2 引入向量检索，使用 **sqlite-vec** 扩展（与主库同文件，零额外服务），配合 SQLite 内置 FTS5 做关键词检索，RRF 融合（§3.2）。

```sql
-- 向量虚拟表（需加载 sqlite-vec 扩展，维度与嵌入模型一致）
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vectors USING vec0(
    item_id TEXT PRIMARY KEY,        -- 关联 knowledge_items.id
    embedding FLOAT[1536]            -- text-embedding-3-small，Cosine 距离
);

-- 全文检索表（contentless 模式，内容仍存 knowledge_items，避免双份存储）
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, content, tags,
    content='knowledge_items', content_rowid='rowid'
);
```

**检索过滤字段**（向量/全文召回后在 SQL 层 JOIN `knowledge_items` 过滤）

| 字段 | 用途 |
|------|------|
| `project_id` | 项目隔离过滤 |
| `content_type` | 文档类型过滤 |
| `roles` | 业务角色过滤（JSON 数组，空数组表示通用；用 `json_each` 匹配） |
| `domain` | 领域标签过滤（test_framework/code_style/prd_template 等） |
| `tags` | 标签过滤 |
| `status` | 仅检索 active（pending_review 不入向量/FTS 索引） |

**维护要点**
- 写入路径：`knowledge_items` 插入/更新（含 `embedding` BLOB）→ 同步更新 `knowledge_vectors` 与 `knowledge_fts`（同一事务）
- 嵌入生成失败时条目降级为仅 FTS 可检索（记 warning 日志，不阻塞写入）
- sqlite-vec 扩展缺失（未安装）时按下方回退链降级，功能可用（§5.4 降级链）

**向量后端抽象与回退（防 sqlite-vec 成熟度风险）**

sqlite-vec 是较新的社区扩展（平台 wheel 覆盖、大规模性能基准有限），因此向量检索通过 `data/vec.py` 的 `VectorBackend` 协议抽象，不硬绑 vec0：

| 后端 | 实现 | 适用 |
|------|------|------|
| `sqlite-vec`（首选） | vec0 虚拟表 cosine 搜索 | 扩展可用的环境 |
| `brute`（内置回退） | 从 `knowledge_items.embedding` BLOB 读全量，numpy 暴力余弦排序 | 个人规模（单项目 ≤ 1 万条）下 < 50ms，零额外依赖 |

设计要点：

- **embedding 的事实来源是 `knowledge_items.embedding` BLOB**，vec0 表仅为可重建的加速索引——回退/切换后端零数据迁移，`knowledge_vectors` 可随时从 BLOB 重建
- 配置 `vector.backend: auto | sqlite-vec | brute`（默认 `auto`：启动时探测扩展，缺失自动切 brute 并记 warning 日志；探测结果写入启动日志）
- 降级链完整形态：sqlite-vec → brute（语义排序保留）→ embedding 生成也失败时纯 FTS5（语义能力降级）
- **FAISS 为可选未来后端**：条目数超 brute 舒适区（> 1 万）或需要 ANN 时再引入，不作为默认依赖（避免重二进制依赖破坏「安装即用」）
- 两后端结果一致性有测试基线（相同查询 TopK 重合度 ≥ 80%，见测试计划 KNO-10）

### 2.4 进程内运行时结构

本地单进程工具无需 Redis。缓存、队列、熔断器状态均为进程内结构，进程退出即失效（均为易失性数据，无持久化需求）。

| 结构 | 实现 | 容量/TTL | 用途 |
|------|------|----------|------|
| 响应缓存 | `cachetools.TTLCache` | maxsize=500, ttl=300s | LLM 响应缓存（key = 请求特征 SHA-256，§3.4） |
| 画像缓存 | `cachetools.TTLCache` | maxsize=50, ttl=600s | UserProfile 缓存（key = `user_id:project_id`） |
| 熔断器状态 | 内存对象（`CircuitBreaker.state`） | 进程生命周期 | 各模型服务熔断状态（§5.1） |
| 异步任务队列 | `asyncio.Queue` | maxsize=1000 | 反馈处理/画像更新等后台任务（§4.3） |
| 并发控制 | `asyncio.Semaphore` | 按配置（默认 10） | LLM 并发请求上限 |

> 进程重启后缓存/熔断状态冷启动：缓存 miss 即回源 LLM/DB，熔断器从 CLOSED 开始——对个人工具均为可接受的瞬态行为，不做持久化。

### 2.5 配置继承链与合并策略

多层级配置，链式继承 + 合并覆盖，由独立 config_merger 模块处理。本地单用户工具无服务端配置层，全部配置来自文件与请求参数。

**配置层级（优先级从低到高）**

1. 默认配置 — 包内 `config/default.yaml`（产品内置基线）
2. 用户全局配置 — `~/.rsi/config.yaml`（跨项目的个人偏好，如默认模型、API Key 引用）
3. 项目配置 — 项目根目录 `rsi-boot.yaml`（schema 见 §10.3.1）
4. 请求参数 — 单次请求携带的 context / preferences 覆盖（仅当次生效，不持久化）

**合并策略**
- 默认合并深度为 2 层（浅合并 + 一层嵌套）
- 列表字段采用替换策略（非追加）
- 关键路径（如 model.endpoint）不允许请求参数层覆盖，仅允许文件层配置
- 合并前校验类型安全（使用 Pydantic schema 约束）；校验失败时回退到上一层级并记 warning

**热加载机制**

| 触发方式 | 实现 | 生效范围 |
|----------|------|----------|
| 文件变更 | watchfiles 监听 `~/.rsi/config.yaml` 与项目 `rsi-boot.yaml` | 当前进程 |
| 进程重启 | 启动时全量加载合并 | 当前进程 |

## 3. 核心算法设计

### 3.1 意图识别

**Phase 1 (M1): 规则版**

基于关键词匹配 + 正则表达式，轻量无依赖，覆盖 80% 常见场景。
M1 规则版输出的是**角色无关的通用意图**（code_review / code_gen / debug / explain / general_assist）。
M2 起由 §3.6 的插件化意图注册接管，通用意图作为所有角色的兜底（fallback）；
通用意图与角色意图的对应关系：code_review → review，code_gen → implement，debug / explain 同名，general_assist → general。

```python
# preprocessor/intent_classifier.py
import re
from typing import Literal

Intent = Literal["code_review", "code_gen", "debug", "explain", "general_assist"]

INTENT_RULES: list[tuple[str, str, Intent, float]] = [
    ("keyword", "review|code review|审阅|审查", "code_review", 0.85),
    ("keyword", "implement|generate|write|create|实现|生成|创建", "code_gen", 0.80),
    ("keyword", "debug|fix|bug|error|wrong|调试|修复|错误|问题", "debug", 0.85),
    ("keyword", "explain|what is|how does|解释|说明|什么是", "explain", 0.80),
]

def detect_intent(raw_input: str) -> tuple[Intent, float]:
    for pattern_type, pattern, intent, confidence in INTENT_RULES:
        if re.search(pattern, raw_input, re.IGNORECASE):
            return intent, confidence
    return "general_assist", 0.5
```

**Phase 2: LLM few-shot 升级**

使用 LLM（gpt-4o-mini）进行意图分类，通过 few-shot 示例提升准确率。LLM 调用失败时降级到规则版。

**实现细节**：

| 项 | 设计 |
|----|------|
| 提示词结构 | system（意图清单 + 各意图定义 + 输出格式约束）→ few-shot 示例（每个意图 2 条，从 `config/intent_examples.yaml` 加载，随角色配置可覆盖）→ user（raw_input，截断至 2000 字符） |
| 输出契约 | 强制 JSON：`{"intent": "<intent>", "confidence": 0.0~1.0}`；解析失败视为调用失败走降级 |
| 模型参数 | gpt-4o-mini，temperature=0，max_tokens=50，超时 3s |
| 置信度门槛 | LLM 返回 confidence < 0.6 时丢弃结果，回退为 `general_assist`（0.5） |
| 降级链 | LLM 超时/失败/JSON 解析失败 → 规则版 detect_intent()；规则版兜底永远可用 |
| 结果缓存 | `rsi:cache:intent:{md5(raw_input[:512])}`，TTL 300s，命中直接返回（相同输入意图不变） |
| 角色感知 | M2 起请求携带 role 时，意图清单替换为该角色的注册意图集（§3.6），few-shot 示例同步切换 |

---

### 3.2 知识检索（混合检索）

**分阶段可用性（v2.6）**：M1 交付 FTS5-only 子集（BM25 关键词检索 + TopN 注入 + token 预算，零新增依赖，P1.18），知识来源为 `rsi knowledge add` 手动种子；Phase 2 在同一注入路径上升级为混合检索（+ 向量召回 + RRF + 置信度门槛，P2.1/P2.3）。

Phase 2 实现混合检索策略，结合 BM25（关键词）和向量检索（语义），通过 RRF（Reciprocal Rank Fusion）融合排序。

**检索流程**

```
用户查询
   |
   +-- BM25 检索 (FTS5)              -> TopK 候选
   +-- 向量检索 (sqlite-vec cosine)   -> TopK 候选
   |
   +-- RRF 融合排序 -> 最终结果 (TOP_N)
```

**BM25 检索**：使用 SQLite FTS5 内置 BM25（`knowledge_fts` 表，§2.3），中文分词依赖 jieba 预处理后写入 FTS。

**向量检索**：使用 text-embedding-3-small 生成查询向量，sqlite-vec cosine 搜索（`knowledge_vectors` 虚拟表，§2.3）。

**RRF 融合**：`score = sum(1 / (k + rank + 1))`，其中 k=60。RRF 仅用于融合排序——其原始分数空间约为 (0, 0.033]（k=60 时理论最大值 2/61），不适合做绝对阈值判断。

**注入门槛**：改用检索阶段的原始分数判断——向量 cosine 相似度 >= 0.6 或 BM25 归一化分数 >= 0.6 的条目才注入并标注来源；融合排序后每个请求最多注入 5 条，注入内容总 token 数 <= 2000（超出时按分数从低到高裁剪）。

**检索参数表**

| 参数 | 值 | 说明 |
|------|-----|------|
| BM25 k1 / b | 1.5 / 0.75 | rank_bm25 默认值，Phase 2 预留调优 |
| BM25 归一化 | `score / max(批内 score)` | 批内 min-max 归一化到 [0,1] 后与阈值比较 |
| Embedding 模型 | text-embedding-3-small（1536 维） | 知识条目写入与查询共用同一模型，禁止混用 |
| 各通道候选数 TopK | 20 | BM25 与向量各取 Top 20 进入 RRF 融合 |
| RRF k | 60 | 经验值，平衡头部与长尾 |
| 注入条数上限 TOP_N | 5 | 融合排序后取前 5 |
| 注入 token 上限 | 2000 | 超出按分数从低到高裁剪 |
| 注入门槛 | cosine ≥ 0.6 或 BM25 归一化 ≥ 0.6 | 原始分数判断，低置信度不注入 |

**过滤与隔离**：向量/全文召回后在 SQL 层 JOIN `knowledge_items` 过滤（`project_id` 必须匹配；`roles` 为空或包含当前角色，用 `json_each` 匹配；`domain` 按请求上下文可选过滤；`status = 'active'`）。

**索引维护**：知识条目写入/更新时在同一事务内同步更新 `knowledge_vectors` 与 `knowledge_fts`（§2.3）；每日离线任务执行 `INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')` 全量重建 FTS 索引（§4.3），防止增量更新累积漂移。

---

### 3.3 策略选择

策略选择分为两步：确定路由 + 概率探索（Thompson Sampling）。

**确定路由**：根据 intent + user_profile + project_config 匹配策略规则。

**Thompson Sampling 设计**

| 组件 | 说明 |
|------|------|
| Arms | 每条候选策略为一个 arm。同一 intent 下可配置多条策略（不同模型/模板组合） |
| Reward | 用户反馈评估，按 action 映射（完整映射表见 §4.2）：采纳/应用 +2，复制/好评(4-5分) +1，修改 +1，参考 +0.5，差评(1-2分) -1，拒绝 -1，忽略 0 |
| Prior | Beta(alpha=1, beta=1) 均匀先验 |
| Update Rule | reward > 0 时 `alpha += reward`；reward < 0 时 `beta += abs(reward)`；reward = 0 不更新。从每个 arm 的 Beta(alpha, beta) 分布采样，选择采样值最大的 arm |
| 状态持久化 | alpha / beta / exposure_count 存 `strategy_configs` 表（§2.2），Feedback Worker 更新；运行时读取走内存缓存（60s 刷新），不阻塞主链路 |
| 离线衰减 | 每周策略调优任务（§4.3）将 alpha/beta 向先验回归：`alpha = 1 + (alpha - 1) × 0.9`，beta 同理——防止历史数据锁定导致策略永久固化 |
| 淘汰规则 | 曝光 ≥ 100 且采纳率 < 20% 的策略标记 `is_active=false`（人工复核后删除） |

> 注：纯 Thompson Sampling 不需要额外的 epsilon 随机探索——样本量小的 arm 其 Beta 分布方差大，探索自然发生。

---

### 3.4 提示词渲染

使用 Jinja2 模板引擎渲染提示词，支持模板继承和变量注入。

**模板目录结构**（与 §11 目录结构一致）

```
config/templates/
+-- base.jinja2
+-- code_review.jinja2
+-- code_gen.jinja2
+-- debug.jinja2
+-- explain.jinja2
+-- general_assist.jinja2
```

**变量注入**：project_name、project_context、user_expertise、user_style、knowledge_items、code_content、language 等。

**上下文截断**：`chat_history` 最多保留最近 10 轮，超出部分截断；渲染后总 prompt 超出模型上下文窗口时，保留 system 消息 + 最近 2 轮历史，其余裁剪。

---

### 3.5 质量评估

Phase 3 实现三维度质量评估。评估标准可随角色动态调整，通过配置定义各角色的维度权重。

| 维度 | 默认权重 | 评估方法 |
|------|----------|----------|
| 相关性 | 0.4 | 回答与问题的语义匹配度（embedding cosine） |
| 准确性 | 0.35 | 事实一致性（LLM-as-judge 检查） |
| 可操作性 | 0.25 | 是否包含可执行的代码/步骤（规则检测） |

**各维度计算方法**：

| 维度 | 计算细节 | 成本与采样 |
|------|---------|-----------|
| relevance | text-embedding-3-small 分别嵌入 query 与 response（各截断 4000 字符），cosine 相似度经 `max(0, (s - 0.2) / 0.6)` 重标定到 [0,1]（cosine 0.2 以下视为不相关，0.8 以上视为满分） | 每次响应都计算（embedding 成本低） |
| accuracy | LLM-as-judge：gpt-4o-mini，temperature=0，提示词给出 query + response + 检索到的知识条目，要求判断事实一致性并输出 `{"score": 0.0~1.0, "reason": "..."}` | **抽样 10%** 的响应执行；用户差评（rating ≤ 2）的响应必查 |
| actionability | 规则检测加权：含带语言标记的代码块 +0.5；含分步骤列表（`1.` / `-` 枚举 ≥ 3 项）+0.3；正文长度 ≥ 意图下限（code_gen ≥ 100 字符，explain ≥ 200 字符）+0.2；按 intent 类型启用不同规则子集 | 每次响应都计算（纯规则无成本） |

综合分：`quality_score = Σ(weight_i × score_i)`，权重按角色取自 `config/quality_rubric.yaml`；accuracy 未采样时按其余两维度权重归一化折算。

**角色权重配置（config/quality_rubric.yaml）**

| 角色 | relevance | accuracy | actionability | 说明 |
|------|-----------|----------|--------------|------|
| developer | 0.3 | 0.3 | 0.4 | 开发者更看重可操作性（代码可执行性优先） |
| test | 0.3 | 0.4 | 0.3 | 测试场景准确性最关键 |
| pm | 0.5 | 0.3 | 0.2 | 产品经理更看重相关性 |
| default | 0.4 | 0.35 | 0.25 | 特殊角色未匹配时用此默认权重 |

**自定义角色扩展：**在配置文件中新增一个角色条目即可，无需修改代码。

质量分数随 `RSIResponse.metadata.quality_score` 返回，客户端可根据分数决定展示方式。

### 3.6 意图系统 — 插件化注册架构 (v0.4+)

**设计原则**：意图识别不硬编码，而是通过配置注册。

#### 内置三套意图模板

| 角色 | 预置意图 | 来源 |
|------|----------|------|
| **Developer** | explain, debug, refactor, testing, implement, review, devops, howto, documentation | 系统内置 |
| **Test Engineer** | test_gen, test_review, bug_analyze, test_data, coverage_check | 系统内置 |
| **Product Manager** | req_analyze, prd_gen, doc_review, data_analysis, stakeholder_comm | 系统内置 |

#### 注册方式

意图通过 YAML 配置注册，无需修改代码：

```yaml
# config/roles/test.yaml
role: test
intents:
  - name: test_gen
    patterns:
      - "(?i)generate test|write test|create test case|test scenario"
    fallback: general
    prompt_template: test_generation.jinja2
  - name: bug_analyze
    patterns:
      - "(?i)analyze bug|bug analysis|root cause|why did this fail"
    fallback: debug
    prompt_template: bug_analysis.jinja2
```

#### 运行时行为

1. 请求携带 `role` 字段
2. IntentRegistry 根据 role 加载对应的配置意图列表
3. 先匹配 role-specific 意图，再匹配通用意图
4. 无匹配时回退到 general
5. role=null 时仅使用通用意图（开发者兼容模式）

#### 添加新角色

用户通过配置文件即可添加新角色意图，无需修改核心代码：

```yaml
# config/roles/operator.yaml
role: operator
intents:
  - name: deploy_check
    patterns: ["(?i)check deployment|verify release|deploy status"]
    fallback: general
    prompt_template: operator_deploy.jinja2
```
### 3.7 响应格式化 — 角色感知渲染

**设计原则**：同一模型输出根据用户角色采用不同的格式化策略，让输出更贴合角色习惯。

#### ContentItem 的角色标记

每个 `ContentItem` 可附带 `applicable_roles` 字段（主定义见 §2.1），用于标记该内容块适用于哪些角色。Postprocessor 在格式化时根据角色过滤不相关的内容块：`applicable_roles` 为 None 或包含当前角色时保留，否则丢弃。

#### 格式化规则配置（config/response_format.yaml）

| 参数 | developer | test | pm | default | 说明 |
|-----------|-----------|------|-----|---------|------|
| prefer_markdown_code_blocks | true | true | false | true | 是否用代码块包装代码 |
| max_suggestion_count | 3 | 5 | 3 | 3 | 最大建议条数 |
| show_token_usage | true | true | false | false | 是否显示代理用量 |
| response_style | concise | detailed | balanced | balanced | concise / detailed / balanced |
| include_source_refs | true | true | true | false | 是否附框架来源引用 |

**实现要点**：
- 格式化在 Postprocessor 阶段完成，在模型输出解析之后、质量评估之前
- 角色配置为空时使用 default 格式
- 格式化规则可通过管理 API 热加载（Phase 1 M2 config_merger）
- 同样的模型输出，不同角色看到的格式不同，但内容一致（仅展示方式差异）

---

### 3.8 用户画像构建与更新

画像数据模型见 §2.1 `UserProfile`，初始画像示例见 §10.9.4。本节定义画像「怎么构建、怎么更新、用什么参数与算法」的完整实现细节。

#### 存储与缓存映射

| 层 | 位置 | 说明 |
|----|------|------|
| 持久层 | `user_profiles.profile_data`（JSONB，§2.2） | `UserProfile.model_dump()` 整体序列化；主键 `(user_id, project_id)`，project_id 为 NULL 表示跨项目全局画像 |
| 缓存层 | `rsi:profile_cache:{user_id}:{project_id}`（Hash，TTL 600s，§2.4） | 读路径：缓存 miss → 读库 → 回填；写路径：**写穿**——先 UPDATE 落库，再 DEL 缓存，禁止先删缓存后写库（防并发脏读） |
| 读取合并 | PreprocessHandler | 全局画像与项目画像同时存在时，项目画像字段覆盖全局画像（与 §2.5 配置合并方向一致） |

#### 初始构建推理规则（rsi bootstrap，Phase 1 M2）

bootstrap 扫描信号源后按以下规则推断画像字段，**所有推断均带置信度，低于阈值宁可留空也不猜测**：

| 画像字段 | 信号源 | 推理规则 | 阈值/参数 |
|---------|--------|---------|----------|
| `expertise` | Git 文件归属 + AST import 统计 | git blame 行数占比 ≥ 20% 的模块 → 模块技术栈标签；import 计数 ≥ 10 的框架/库 → 技术标签 | 占比阈值 20%；import 计数阈值 10；单用户标签上限 20 个 |
| `preferences.code_style` | .editorconfig > 代码扫描 > Git log | 标识符命名风格统计（snake_case / camelCase 占比），取多数派 | 多数派占比 ≥ 60% 才判定，否则留空 |
| `preferences.role_specific.language / framework` | 文件扩展名分布 + 构建配置 | 源文件行数占比最高的语言；framework 取自 pyproject.toml / package.json 依赖清单 | 语言占比 ≥ 40% 判定为主语言 |
| `preferences.role_specific.commit_style` | Git log commit message | conventional commits 正则匹配率 | 匹配率 ≥ 70% 判定为 conventional |
| `preferences.role_specific.test_framework` | 测试目录 + 依赖 | tests/ 目录存在性 + pytest/jest 等依赖声明 | 依赖声明优先于目录推断 |
| `project_insights.test_culture` | 测试文件占比 + commit 中 test 比例 | 测试文件数 / 源文件数 | ≥ 30% = strong；10%~30% = moderate；< 10% = weak |
| `project_insights.doc_quality` | docs/ 覆盖率 + README 章节完整度 | README 必备章节（安装/使用/配置）齐备度 + docs/ 文档数 | 齐备度 ≥ 80% 且 docs ≥ 3 篇 = good |
| `history_summary` | LLM 摘要 | 输入：commit subject Top 50 + 目录树（深度 2）+ README 全文；输出 ≤ 500 字 | gpt-4o-mini，temperature=0.3，max_tokens=800 |
| `role` | rsi-boot.yaml > 文件归属推断 | 配置文件显式声明优先；否则按 expertise 标签映射（test 标签占比最高 → test 等） | 推断置信度 < 0.6 时置 None（通用模式） |

#### 运行时增量更新（Phase 2 起，Feedback Worker 执行）

每条反馈处理时按 §4.2 的映射规则更新画像，字段级规则：

| 字段 | 更新规则 | 约束 |
|------|---------|------|
| `frequently_used_intents` | 每次请求对应 intent 计数 +1 | 按 30 天滑动窗口统计，每日离线重算（见下） |
| `preferred_models` | 按模型采纳率降序排列 | 仅统计曝光 ≥ 20 次的模型，避免小样本噪声 |
| `knowledge_interests` | 检索命中文档的 domain/tags 计数 +1 | 保留 Top 20，超出淘汰计数最低项 |
| `preferences.*` | **不**从隐式行为自动改写；仅接受用户显式配置与离线聚合结果 | 防止噪声行为污染偏好 |

#### 时间衰减

行为计数类字段（frequently_used_intents、knowledge_interests、模型采纳/曝光计数）按指数衰减：

```
count_effective = count × 0.5 ^ (days_since_event / HALF_LIFE_DAYS)
HALF_LIFE_DAYS = 30    # 半衰期 30 天：一个月前的行为权重减半
```

衰减在每日离线聚合时统一重算（而非每次写入时衰减），保证写入路径 O(1)。

#### 离线聚合算法（§4.3「画像更新」任务，每日执行）

```python
def rebuild_profile(user_id: str, project_id: str | None) -> UserProfile:
    logs = query_logs(user_id, project_id, days=90)        # 近 90 天交互 + 反馈
    profile = load_or_default(user_id, project_id)

    # 1. 意图频率：衰减后重算
    profile.frequently_used_intents = decayed_count(logs, key="intent")

    # 2. 偏好模型：采纳率 = (accepted + 0.5 × modified) / 总反馈，曝光 >= 20 才纳入
    profile.preferred_models = rank_by_accept_rate(logs, min_exposure=20)

    # 3. 知识兴趣：检索命中 domain/tags 衰减计数，取 Top 20
    profile.knowledge_interests = decayed_top_k(logs, key="retrieval_hits", k=20)

    # 4. 画像未覆盖的偏好字段保持现状（离线任务不猜测用户偏好）
    profile.updated_at = utcnow()
    return profile  # 写穿：UPDATE user_profiles + DEL redis 缓存
```

#### 冷启动与兜底

- 无画像记录：使用 `UserPreferences` 默认值 + 通用模式（role=None），系统功能完整可用，仅无个性化
- bootstrap 未执行：画像从首次交互开始按运行时规则逐步积累
- 画像读取失败（DB/缓存均不可用）：降级为默认画像，不阻塞主链路（§5.4 降级链 Level 2）

#### 参数速查表

| 参数 | 值 | 位置 |
|------|-----|------|
| 画像缓存 TTL | 600s | §2.4 |
| 行为半衰期 | 30 天 | 本节 |
| 离线聚合窗口 | 90 天 | 本节 |
| 模型采纳率最小曝光 | 20 次 | 本节 |
| 知识兴趣上限 | Top 20 | 本节 |
| expertise 归属阈值 | 20% / import ≥ 10 | 本节 |
| 画像更新周期 | 每日 | §4.3 |

### 3.9 Harness 自我改进（Harness-RSI）

RSI Boot 的「RSI」即 Recursive Self-Improvement（递归自我改进）。遵循 2026 年 Harness-RSI 路线（MetaRSI-v1 / Self-Harness / AHE 等）：**模型权重不动，把 Harness 本身作为可写面**——用执行轨迹反馈驱动「评估 → 挖掘 → 提案 → 验证 → 晋升」闭环。

与 §3.3 Thompson Sampling 的分工：Thompson 在**既有策略候选**间做探索/利用；本节机制负责**创造与淘汰候选本身**（对槽位做加法/减法/修改）。§4.3 的知识提取与策略调优是本框架在 knowledge / strategy 槽位的子集，本节为其上位框架，统一提案、验证与版本化语义。

#### 可写槽位模型（6 槽）

| 槽位 | 载体 | 加法 | 减法 | 修改 |
|------|------|------|------|------|
| prompt_template | `config/templates/*.jinja2` | 新增模板 | 停用引用 | 修订（存 diff） |
| skill | `config/skills/<name>/SKILL.md`（frontmatter + 指令，可选脚本/资源文件） | 新增技能 | 停用/删除技能 | 修订指令或触发条件 |
| strategy | `strategy_configs` 行 | 新策略候选 | `is_active=0` 淘汰 | weight/模型/模板引用调整 |
| intent_rule | `config/intent_rules.yaml` | 新规则 | 移除无效规则 | 置信度/关键词调整 |
| knowledge | `knowledge_items` | §4.3 知识提取 | `status=rejected/archived` | 内容修订 |
| profile | `user_profiles` | — | — | §3.8 衰减与聚合（不经提案流程） |

与 MetaRSI-v1 五槽位（System Prompt / Skill / MCP / Tools / Memory）的映射：`prompt_template` ≈ System Prompt，`skill` ≈ Skill，`knowledge` + `profile` ≈ Memory，`strategy`/`intent_rule` 是检索与路由层策略（MetaRSI 无对应物，属本系统特有槽位）。

**关于 Skill / Tools / MCP 三槽的修正说明（v2.3）**：v2.2 曾以「自身即 MCP server、工具面固定」为由判定三槽不适用，该论断不成立——Superpowers 同为 MCP 形态，证明了「薄的固定工具层 + 厚的数据驱动技能层」可行：技能是数据（文件型指令包）而非代码，增删技能不需要改代码。因此：

- **Skill 槽：纳入可写面**（本表 skill 行）。技能 = 程序性记忆（"怎么做"），与 knowledge 的事实性记忆（"是什么"）互补，是自我改进最高频的产出物。文件型设计（SKILL.md 风格）使其天然可走提案流程、进配置快照、随 bundle 导出
- **Tools 槽：记为未来路径**。声明式工具（yaml 定义的 HTTP 调用/提示词链，经校验注册）技术上可行，但技能文件已可编排「何时调用哪个已有工具」，优先级低，需要时再立项
- **MCP 槽：刻意不做，但属范围取舍而非形态必然**——聚合/代理其他 MCP server 是网关方向，与「不做网关」的定位冲突（§10.7）

**技能槽运行时**：`config/skills/` 目录随配置热加载（§2.5）被发现；每个技能的 frontmatter 声明 name/description/trigger（意图或关键词）；查询时由策略引擎按 trigger 匹配，命中技能的指令体注入提示词（与知识注入共用 §3.2 的预算与标注机制，来源标注为 `skill:<name>`）；技能内容变更与模板一样经提案流程与快照版本化。

**生产/消费关系**：技能的消费者是**下游调用方 Agent**（指令随增强提示词返回，指导其行为，与 Superpowers 技能同类，仅触发方式为自动匹配而非显式调用）；Harness-RSI 闭环是技能的**生产者/管理者**（失败挖掘 → 技能提案 → 门禁晋升），闭环自身的行为由代码固定、不消费技能——否则改进机制将可被自身产出物驱动，破坏元级修改禁令（见安全边界）。

#### 提案生命周期

```
proposed → validating → approved ──→ active ──(7 天观察期恶化)──→ rolled_back
               │            │
               ▼            ▼
           rejected     rejected（人工否决）
```

全程落库 `harness_proposals` 表（§2.2），每步可审计、可回滚。

#### 提案生成与验证（每周离线任务，§4.3）

1. **失败模式挖掘**：聚类近 7 天负信号（rejected、rating ≤ 2、quality_score < 0.5、fallback 触发），按 intent × strategy 分桶，仅处理样本数 ≥ 5 的桶（避免偶发噪声）
2. **提案生成**：LLM（gpt-4o-mini，temperature=0.2）对每个失败桶生成 ≤ 3 个**最小化、单槽位**提案——每个提案必须绑定具体失败机制（Self-Harness 约束），禁止泛化大改
3. **回归验证门禁**：候选改动在回放集（近 30 天该 intent 抽样 50 条请求，含历史反馈标签）上重放；晋升条件 = 目标指标（采纳率/质量分）提升**且无任何维度退化 > 5%**，否则 rejected
4. **人工确认**：通过门禁的提案进入 `approved`，经 `rsi_review` 工具确认后 `active`；信任度建立后可在 `rsi-boot.yaml` 配 `auto_apply: true` 跳过人工（不推荐初期开启）

除每周离线任务外，`rsi_review` 提供 `generate` 操作支持**按需触发**提案生成（借鉴 RSI-Harness 的交互式蒸馏）：立即对近 7 天负信号执行挖掘与提案生成，仍受单轮 ≤ 5 个提案的速率上限约束。

#### 版本化与回滚（Genome 式整体快照）

借鉴 RSI-Harness 的 Genome 模式：**提案层存 diff，版本层存整体快照，回滚 = 切换快照**（而非逆向重放 diff）。

- 提案的 `payload` 仍存 before/after diff（审计粒度）；每次提案**晋升生效时**，将 6 槽位当前内容整体导出写入 `config_snapshots`（§2.2），版本号项目内单调递增
- 快照合并语义与 §2.5 配置链一致：**缺省 = 继承上一版本；`null` = 显式重置回默认；有值 = 覆盖**（对象递归合并、数组整体替换）——单槽位小改不需要复制全量内容
- 回滚（`rsi_review` rollback 或观察期自动触发）= 将目标历史快照标记 `rolled_back_to` 并以其内容重建槽位，原快照转 `superseded`；任意历史版本可切换
- 快照可导出为目录型 bundle（`genome.json` 清单 + 各槽位文件），用于跨项目迁移或备份——个人工具的「换项目即换 Genome」
- 晋升后进入 **7 天观察期**：每日巡检，指标显著恶化（采纳率下降 > 5%）自动回滚到晋升前快照并标记提案 `rolled_back`

#### 安全边界（防递归失控）

- `interaction_logs` 为 append-only 事实日志，改进机制只读、永不改写（canonical event log 原则）
- 提案只能改 6 个槽位的内容，**不能改**：评估代码、回归门禁参数、提案机制自身——元级修改只能人工进行
- 改进速率硬上限：单轮提案 ≤ 5 个、单槽位周变更 ≤ 3 次（防振荡）

**与 MetaRSI-v1 的刻意取舍**：MetaRSI 的 RSI² Agent 支持纵向优化（改写算子自身的提案策略）与 Model-RSI（训练内化权重）。本系统**刻意不做**：纵向元级优化对个人工具风险收益比过低，保持人工独占元级修改权；Model-RSI 因消费 LLM API 而非自训模型天然不适用。但吸收其「内化后做减法」的推论——知识去重与归档（§4.3）即本系统的减法对应物。若未来引入纵向优化，应以只读建议形式呈现「提案策略的提案」，仍走人工确认门禁。

## 4. 数据流程设计

### 4.1 主请求处理流程（Pipeline 模式）

RSI Boot 采用 Pipeline 模式编排请求处理流程，每个阶段为独立 Handler，通过 Orchestrator 串联。

```
+-----------+    +-----------+    +-----------+    +-----------+    +-----------+
|Preprocess |--->| Strategy  |--->|   Model   |--->|Postprocess|--->|   Log     |
| Handler   |    | Handler   |    |  Adapter  |    | Handler   |    | Handler   |
|           |    |           |    |           |    |           |    |           |
| Intent    |    | Route     |    | Call LLM  |    | Parse     |    | Persist   |
| Context   |    | Template  |    | SSE stream|    | Format    |    | Cost calc |
| Retrieval |    | Thompson  |    | Fallback  |    | Feedback  |    |           |
+-----------+    +-----------+    +-----------+    +-----------+    +-----------+
```

**日志两段式写入**：feedback_token 在 Preprocess 阶段随 request_id 一并生成；Log Handler 在模型调用**之前**先 INSERT `status='pending'` 的日志记录（含 request_id 与 token），响应完成后再 UPDATE 为终态（success/error + 成本与延迟）。这样即使服务在模型调用后崩溃，token 也已持久化可关联；遗留的 pending 记录由离线对账任务扫描补齐（见 §4.3）。

### 4.2 反馈处理流程（异步任务）

**分阶段可用性（v2.6）**：M1 交付 `rsi_feedback` 基础版（P1.18）——显式评分/评论同步落库，无异步处理；异步队列消费、隐式事件、画像/策略更新在 Phase 2/3 逐步接入（P2.5/P3.1/P3.2）。数据自 M1 起积累，为 Phase 3 挖掘与回归门禁预攒回放集。

反馈处理投递到进程内 `asyncio.Queue`（§2.4），由后台 task 消费，不阻塞主推理链路。进程退出时队列中未消费的反馈丢失——反馈是增量信号，丢失少量可接受；显式反馈（rating/rejected）在 MCP 响应返回前同步落库日志，仅画像/策略更新走异步队列。

```
+----------+    +--------------+    +-------------+    +------------+
| MCP      |--->| asyncio.Queue |--->|  Feedback   |--->|  SQLite    |
| rsi_     |    | (进程内)      |    |  Task       |    |  Update    |
| feedback |    |              |    | Valid Token  |    | Log update |
+----------+    +--------------+    | Strategy    |    | Profile    |
                                    | Update      |    | update     |
                                    +-------------+    +------------+
```

**知识提取触发**：`modified` 类反馈中，修改后内容与原文的 diff 比例 > 20% 时，自动生成知识提取候选进入人工确认队列（Phase 3，见 §4.3）。

**反馈字段级更新规则**：Feedback Worker 校验 token 后，按 action 执行以下映射（reward 数值喂给 §3.3 Thompson 更新；画像字段更新规则详见 §3.8）：

| action | 策略 reward（§3.3） | 画像更新（§3.8） | 日志更新 | 其他 |
|--------|--------------------|------------------|----------|------|
| accepted | +2 | intent 计数 +1；模型采纳 +1 | feedback_action=accepted | — |
| applied | +2 | 同 accepted | feedback_action=applied | — |
| modified | +1 | intent 计数 +1；模型采纳 +0.5；记录 diff 比例 | feedback_action=modified | diff > 20% → 知识提取候选 |
| copied | +1 | knowledge_interests 命中标签 +1 | feedback_action=copied | — |
| referenced | +0.5 | knowledge_interests 命中标签 +1 | feedback_action=referenced | — |
| rejected | -1 | 模型拒绝 +1 | feedback_action=rejected | rating ≤ 2 时触发 accuracy 必查（§3.5） |
| ignored | 0 | 模型曝光 +1 | feedback_action=ignored | — |
| rating（1-5 分） | 4-5 分 +1；1-2 分 -1；3 分 0 | — | feedback_rating 写入 | 与 action 可叠加（同一反馈同时携带时分别生效） |

**隐式行为上报协议**：采纳/修改/复制/应用等 IDE 侧行为由客户端通过 `rsi_feedback` 上报（action 为对应隐式动作，携带 feedback_token）。MCP 协议本身不感知用户行为，由 IDE 集成层（MCP 客户端配置、CLI hook 等）负责捕获并上报；无客户端集成时隐式指标不适用，仅统计显式反馈。

### 4.3 离线分析任务

离线任务由进程内调度器（`asyncio` 定时任务）执行，进程运行期间按周期触发；进程未运行则跳过，下次启动时补跑当日任务（以 `user_profiles` 外的内部状态表记录上次执行时间）。

| 任务 | 周期 | 输入 | 输出 |
|------|------|------|------|
| 日志归档 | 每日 | interaction_logs（超过 90 天保留期） | 导出为 `.rsi/archive/` 下 JSONL 文件后从库中删除（§8.3 数据生命周期） |
| pending 日志对账 | 每小时 | status='pending' 且超过 10 分钟的记录 | 补齐 UPDATE 或标记为异常 |
| 采纳率统计 | 每日 | interaction_logs | 各意图/策略采纳率报表 |
| 成本统计 | 每周 | interaction_logs | Token 用量与费用汇总（§6.1） |
| 画像更新 | 每日 | interaction_logs + feedback | 按 §3.8 离线聚合算法重算画像（衰减 + TopK + 采纳率），写穿落库并失效缓存 |
| 策略调优 | 每周 | strategy_configs 的 alpha/beta/exposure_count | alpha/beta 向先验回归（×0.9）；曝光 ≥ 100 且采纳率 < 20% 的策略标记 is_active=0（§3.3） |
| FTS 索引重建 | 每日 | knowledge_items 全量 | `knowledge_fts` rebuild，修正增量漂移（§3.2） |
| 知识提取 | 每日 | 候选队列（modified diff>20%、rating ≥ 4 的交互） | 候选知识条目进入人工确认队列（见下） |
| 失败模式挖掘 | 每周 | interaction_logs 负信号（rejected/rating≤2/quality<0.5/fallback） | intent×strategy 失败模式桶（§3.9） |
| Harness 提案生成与回归验证 | 每周 | 失败模式桶 + 近 30 天回放集 | harness_proposals 状态流转（§3.9） |
| 提案观察期巡检 | 每日 | 处于 7 天观察期内的已晋升提案 | 指标恶化 > 5% 自动回滚（§3.9） |

**知识提取流程（Phase 3）**：

1. **候选汇集**：modified 反馈 diff>20% 的（问题, 修改后内容）对；rating ≥ 4 的高质量交互
2. **LLM 提取**：gpt-4o-mini（temperature=0.2）从候选对生成知识条目草稿（title / content / tags / domain / roles），要求提炼「可复用的经验规则」而非流水账
3. **去重**：草稿与现有 knowledge_items 做向量比对，cosine ≥ 0.9 视为重复——合并入现有条目（更新 updated_at），不产生新条目
4. **人工确认**：草稿以 `status='pending_review'` 入库，经 `rsi_knowledge` 工具的 review 操作确认后转 `active` 并生成 embedding 进入检索；拒绝则转 `rejected` 留存 30 天后清理

---


## 5. 错误处理与熔断设计

### 5.1 熔断器（Circuit Breaker）

三态熔断器，纯进程内内存状态（§2.4），无持久化、无跨进程共享——本地单进程工具不需要。进程重启后所有熔断器从 CLOSED 冷启动，可接受。

**失败计数口径**：

- **计入失败**：连接错误、读/写超时、HTTP 5xx、未捕获异常
- **不计入失败**：HTTP 4xx（请求本身有问题，非服务故障）、业务校验失败、熔断器 OPEN 期间被拒绝的请求
- 计数方式为**连续失败计数**——任何一次成功立即清零；不使用滑动窗口（实现简单、语义明确，阈值语义 = 「连续 N 次失败」）

**状态机**：

```
CLOSED ──连续失败 ≥ threshold──▶ OPEN ──距 opened_at ≥ recovery_timeout──▶ HALF_OPEN
  ▲                                                                │
  │◀──────────── 探测成功（计数清零）──────────────┤
  │                                                                ▼
  │◀──────────── 探测失败（重新计时 recovery_timeout）──── OPEN ◀──┘
```

- **CLOSED**：正常放行，同步记录成功/失败
- **OPEN**：fail-fast，请求不实际发起，直接抛 `CircuitOpenError` 交由 §5.4 降级链处理
- **HALF_OPEN**：放行 **1 个**探测请求（并发请求中仅第一个放行，其余 fail-fast）；探测成功 → CLOSED 并清零计数；失败 → 回到 OPEN 并重新计时
- 状态计时使用单调时钟（`time.monotonic()`），不受系统时间跳跃影响
- 状态存储：内存对象属性 `{"state", "failure_count", "opened_at_ms"}`——状态必须显式转换，不得因过期丢失

### 5.2 DataServiceProxy

封装所有外部依赖调用（SQLite、LLM API、Embedding API），统一处理连接失败、超时和降级。每个依赖有独立的熔断器实例。

| 依赖 | 熔断阈值 | 恢复超时 | 降级策略 |
|------|----------|----------|----------|
| sqlite | 3 | 30s | 日志/画像读写失败时跳过持久化，本次请求仅走内存（响应照常返回，记 error 日志） |
| embedding | 3 | 30s | 跳过向量检索，仅用 FTS5 关键词检索（§3.2） |
| llm_primary | 5 | 60s | 尝试 fallback 模型（Phase 2：同厂商低价型号，如 gpt-4o → gpt-4o-mini；Phase 4：跨厂商） |

### 5.3 重试策略

| 场景 | 策略 | 最大重试 | 超时 |
|------|------|----------|------|
| LLM 调用 | 指数退避 base=1s + full jitter | 3 | 60s |
| SQLite 写入 | 指数退避 base=0.1s + full jitter（WAL 下写锁竞争短暂） | 3 | 10s |
| Embedding 调用 | 指数退避 base=0.5s + full jitter | 2 | 30s |

**退避算法（full jitter，防重试风暴同步）**：

```python
sleep = random.uniform(0, min(cap, base * 2 ** attempt))
# LLM 调用示例：base=1s, cap=30s → 第 1/2/3 次重试分别在 [0,2s] / [0,4s] / [0,8s] 内随机
```

**可重试与不可重试**：

| 类别 | 错误 | 处理 |
|------|------|------|
| 可重试 | 连接错误、读超时、HTTP 429 / 5xx、SQLite `SQLITE_BUSY` | 按上表退避重试 |
| 不可重试 | HTTP 4xx（除 429）、LLM API Key 无效、请求校验错误、响应解析错误、SQLite 约束冲突 | 立即失败，计入熔断器失败计数（4xx 除外，见 §5.1 口径） |

**幂等约束**：仅对幂等操作重试——查询类直接重试；LLM 调用携带 request_id 供日志去重；**流式响应一旦已开始输出 token 即不再重试**（重试会导致重复输出），失败直接走降级链。

### 5.4 降级策略链

全功能 → 无知识检索/画像 → 无持久化 → 仅 LLM 直连 → 返回静态降级消息

| 级别 | 触发条件（对应熔断器 OPEN） | 可用功能 | 不可用功能 |
|------|---------------------------|----------|------------|
| Level 0 (正常) | 全部 CLOSED | 全功能 | 无 |
| Level 1 | embedding OPEN | 基础 + FTS5 关键词检索 + 日志 | 向量语义检索 |
| Level 2 | sqlite OPEN | 基础模型调用 + 内存态画像/缓存 | 日志持久化、画像读取、反馈落库 |
| Level 3 | llm_primary OPEN | fallback 模型直连 | Pipeline 编排中的增强环节（检索/画像注入跳过） |
| Level 4 | 全部 LLM（含 fallback）OPEN | 返回静态降级消息 | 所有外部依赖不可用 |

**恢复方向**：级别由请求处理时实时评估各熔断器状态得出，无独立状态机——对应依赖熔断器回到 CLOSED 后，下一请求自动恢复更高级别，无需人工介入。

## 6. 成本控制

个人工具的轻量成本意识：用量统计 + 可选每日 token 上限。无多租户、无费用分摊。

### 6.1 成本核算

每次请求完成后根据模型单价 + Token 用量计算 USD 成本。

| 模型 | Prompt 单价 (/1M tokens) | Completion 单价 (/1M tokens) |
|------|------------------------|-----------------------------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4-turbo | $10.00 | $30.00 |

> Phase 1 仅接入 OpenAI，单价表随模型注册中心配置可扩展；Phase 4 接入 Anthropic / DeepSeek 后补充对应单价。

**计量与计算公式**：

- **Token 来源**：优先取 API 响应的 `usage` 字段（`prompt_tokens` / `completion_tokens`，精确值）；流式响应若末帧无 usage，则用 `tiktoken`（cl100k_base 编码）对 prompt 与拼接后的响应文本估算，并在日志 `context.token_estimated=true` 标记
- **公式**：`cost_usd = prompt_tokens / 1e6 × prompt_price + completion_tokens / 1e6 × completion_price`，结果写入 `interaction_logs.cost_usd`（REAL，未定价为 NULL）
- **未定价模型**：单价表查不到时 `cost_usd = NULL`，token 用量照常记录，并输出告警日志（防止新接入模型漏计价）
- **单价配置**：`config/models.yaml` 按模型名维护，热加载生效（§2.5），改价不影响历史记录

### 6.2 预算控制（可选）

- 每日 Token 上限：`rsi-boot.yaml` 配置 `budget.daily_token_cap`（默认不开启）；超限后当日后续请求返回降级提示（含当日已用量），不强制阻断——再次调用时客户端可带 `preferences.force=true` 覆盖
- 意图级成本倾向：code_gen 可用高价模型，general_assist 仅允许低价模型（策略路由规则体现，非硬限制）

**实现细节**：

- **用量计数**：直接查 SQLite——`SELECT COALESCE(SUM(total_tokens),0) FROM interaction_logs WHERE created_at >= 当日 00:00 UTC`；`created_at` 有索引（§2.2），个人量级下毫秒级，无需额外计数器
- **检查时机**：策略选择**之前**做检查（当日累计 + 该 intent 近 7 天平均 token 预估），预估超限即返回降级提示，而非事后发现
- **提醒**：用量达上限 80% 时在响应 `metadata.budget_warning` 中附带提示（客户端可选择性展示）

### 6.3 用量查询

所有成本数据记录在 `interaction_logs`，提供 `rsi_stats` 工具（P3）按日/周/月汇总 token 与成本，支持按意图、模型分组。数据不出本地，导出走 §8.3 数据生命周期。

## 7. MCP 工具接口契约

### 7.1 工具列表

| 工具名 | 阶段 | 说明 |
|--------|------|------|
| rsi_query | P0 (M1) | 统一查询入口 |
| rsi_feedback | P0 (M1) | 统一反馈入口：显式评分 + 客户端隐式行为事件上报（见 §4.2 隐式行为上报协议） |
| rsi_knowledge_add | P2 | 添加知识条目 |
| rsi_knowledge_search | P2 | 搜索知识库 |
| rsi_knowledge_delete | P2 | 删除知识条目 |
| rsi_review | P2 | Harness 改进提案的列表/确认/拒绝/回滚/按需生成，及配置快照的列表/切换/导出（§3.9） |
| rsi_stats | P3 | 用量与成本统计查询（§6.3） |

> 命名约定：工具名统一为 `rsi_` 前缀 + 动词原形（`rsi_query` / `rsi_feedback`），文档与实现保持一致。

**工具 Schema 契约**：`tools/list` 返回的每个工具包含 `name` / `description` / `inputSchema`（JSON Schema）。inputSchema 由 §2.1 的 Pydantic 模型自动生成（P1.2 产出 `schemas/*.json`），保证接口契约与数据模型单一事实来源：

| 工具 | inputSchema 来源模型 | 关键入参 |
|------|---------------------|---------|
| rsi_query | `RSIRequest`（§2.1） | raw_input 必填；user_id/project_id/role/context 可选 |
| rsi_feedback | `Feedback`（§2.1） | feedback_token + action 必填；rating/comment/modified_content 可选 |
| rsi_knowledge_add | `KnowledgeItem`（§2.1） | title + content 必填；roles/domain/tags 可选 |
| rsi_knowledge_search | 查询模型（query, top_k ≤ 20, domain/roles 过滤） | query 必填 |
| rsi_knowledge_delete | 删除模型（id 或 title 精确匹配） | id 必填 |
| rsi_review | 提案操作模型（action: list/approve/reject/rollback/generate/snapshot_list/snapshot_switch/snapshot_export） | action + proposal_id 或 version（list/generate 除外） |
| rsi_stats | 统计查询模型（period: day/week/month，group_by: intent/model） | period 必填 |

响应侧：`rsi_query` 返回 `RSIResponse`（§2.1）；其余工具返回 `{"status": "success|error", "data": ..., "error": {...}}` 统一信封。契约测试（测试计划 §9）校验 tools/list 输出与 schemas/*.json 一致。

### 7.2 身份与信任模型

本地单用户工具，**无认证、无权限体系**：仅 stdio 传输，信任本地进程边界（与 Superpowers 等 MCP 工具一致，安装即用）。

- **user_id 解析**：默认取 `git config user.email`；未配置 git 时为 `local`。仅用于画像与日志归属，不用于任何访问控制
- **role 字段**：业务角色（developer/test/pm/自定义），信任客户端传入，仅影响意图路由/知识检索/响应格式化（§2.1）
- **多项目**：project_id 是数据命名空间（各项目画像/知识/策略隔离），不是租户隔离——所有数据本机可见，删除项目数据即删 `.rsi/` 目录

## 8. 安全设计

本地单用户工具的安全目标收敛为两点：**防密钥/敏感信息泄漏进知识库或外发给 LLM**（脱敏），**本地数据可管可控**（生命周期）。无认证、无限流、无传输层服务端（仅 stdio）。

### 8.1 数据脱敏

| 敏感数据类型 | 脱敏规则 | 作用域 |
|-------------|----------|--------|
| API Key / Token | 保留前4位后4位，中间用 **** 替换 | 日志 |
| 用户邮箱 | 用户名部分用 *** 替换 | 日志、响应 |
| IP 地址 | 保留前两段，后两段用 .* 替换 | 日志 |
| 代码片段 | 默认保留（业务需要）；落库与外发 LLM 前执行密钥/Token/内网地址正则扫描（规则见下表，§10.9.7 复用同一规则集），命中部分脱敏后存储/发送 | 日志、外发 |

**脱敏正则模式表**（`core/masking.py` 的 `MASKING_RULES`，日志脱敏与 §10.9.7 敏感信息扫描共用同一定义，单一事实来源）：

| 规则名 | 正则模式 | 替换为 |
|--------|---------|--------|
| email | `\b[\w.+-]+@(([\w-]+\.)+[\w-]{2,})\b` | `***@\1` |
| ipv4 | `\b(\d{1,3}\.\d{1,3})\.\d{1,3}\.\d{1,3}\b` | `\1.*.*` |
| openai_key | `sk-[A-Za-z0-9_-]{20,}` | `sk-****` |
| aws_akid | `AKIA[0-9A-Z]{16}` | `AKIA****` |
| github_token | `(ghp\|gho\|ghu\|ghs\|ghr)_[A-Za-z0-9]{36,}` | `gh*_****` |
| slack_token | `xox[baprs]-[A-Za-z0-9-]{10,}` | `xox*-****` |
| jwt | `eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}` | `eyJ****` |
| private_key | `-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----` | `[PRIVATE KEY REDACTED]` |
| password_field | `(?i)(password\|passwd\|pwd\|secret)\s*[:=]\s*["']?[^\s"']{4,}` | `\1=****` |
| internal_ip | `\b(10\.\d{1,3}\|172\.(1[6-9]\|2\d\|3[01])\|192\.168)\.\d{1,3}\.\d{1,3}\b` | `[INTERNAL_IP]` |

执行时机：Log Handler 写库前对 `raw_input` / 响应内容执行全表扫描；外发 LLM 的 prompt 组装后同样过一遍（防知识库内容带密钥外发）。正则用 `re.compile` 预编译，扫描耗时计入 §9 性能预算（单请求 < 5ms）。

### 8.2 凭证管理

- LLM API Key 通过环境变量（`OPENAI_API_KEY` 等）或 `~/.rsi/config.yaml` 引用环境变量名配置；**禁止写在项目 `rsi-boot.yaml` 中**（该文件可能入库），加载器发现明文 Key 形态（如 `sk-...`）时拒绝启动并提示
- 配置文件建议权限 0600；日志中 Key 一律按 §8.1 脱敏
- `.rsi/` 目录加入项目 `.gitignore` 模板（bootstrap 时自动写入，§10.9）

### 8.3 数据生命周期

| 阶段 | 策略 |
|------|------|
| 创建 | 数据进入系统时记录时间戳 created_at |
| 存储 | 全部存本地 `.rsi/rsi.db`；日志保留 90 天 |
| 访问 | 本机用户直接可查（`rsi_stats` / `rsi_knowledge_search` / 直接打开 DB）；敏感字段脱敏后进入日志 |
| 归档 | 超过 90 天的日志导出为 `.rsi/archive/YYYYMM.jsonl` 后从库中删除（§4.3 离线任务） |
| 删除 | `rsi_knowledge_delete` 删知识条目；卸载即删除整个 `.rsi/` 目录，覆盖全部日志/画像/反馈/归档 |
| 导出 | `rsi_stats` 支持 JSON/CSV 导出用量与日志摘要 |

**可选字段级加密（AES-256-GCM）**：默认关闭。用户处理高敏感代码时可开启——环境变量 `RSI_DATA_KEY`（base64 编码 32 字节）注入后主密钥对 `interaction_logs.raw_input` 加密落库；Python `cryptography` 库 `AESGCM`，每次加密 12 字节随机 nonce（`secrets.token_bytes(12)`，GCM 下 nonce 复用是灾难性的，禁止固定 nonce）；密文格式 `enc:v1:{base64(nonce ‖ ciphertext ‖ tag)}`。密钥丢失则历史 raw_input 不可读，其余字段不受影响。

### 8.4 传输安全

- 与 LLM / Embedding API 的通信强制 HTTPS（SDK 默认）
- stdio 传输（本地进程间）无需加密
- 无 HTTP 服务端，无 TLS 证书管理面

## 9. 测试策略

### 9.1 测试金字塔

| 层级 | 框架 | 目标覆盖率 | 运行频率 |
|------|------|-----------|----------|
| Unit | pytest | 行覆盖率 > 85% | 每次提交 |
| Integration | pytest + pytest-asyncio | > 60% | 每次 PR |
| E2E | pytest（子进程启动 MCP Server，stdio 直连） | 关键路径 | 每次 Release |

> 覆盖率口径：单元测试行覆盖率 > 85%；CI 的 PR 门禁为整体行覆盖率 > 80%（含集成测试未覆盖路径）。两者口径不同，门禁以整体 80% 为准。

> **算法验收基线**：覆盖率只验证代码正确性。RSI 特有算法组件（意图分类、混合检索、Thompson Sampling、Harness 提案门禁、画像推断）另有量化验收基线与版本化评测集（`tests/eval/`），权威数值以**测试计划 §12** 为准；算法相关变更除覆盖率门禁外，还须通过对应算法基线的回归门槛。

### 9.2 单元测试重点

| 模块 | 测试重点 |
|------|----------|
| preprocessor.py | 意图识别规则覆盖、上下文组装 |
| strategy_engine.py | 策略路由逻辑、Thompson Sampling 分布验证 |
| model_adapter.py | LLM 调用、超时、错误处理 |
| orchestrator.py | Pipeline 编排、错误传播 |
| config_merger.py | 层级合并、受保护键、类型校验 |
| circuit_breaker.py | 状态转换（CLOSED/OPEN/HALF_OPEN）、单调时钟计时 |
| data_service_proxy.py | 降级逻辑、fallback 调用 |
| masking.py | 各类脱敏规则 |
| migrations | PRAGMA user_version 迁移幂等性、中断重入 |

### 9.3 集成测试

| 测试 | 组件 | 依赖 |
|------|------|------|
| 数据库 CRUD | LogService | 临时目录 SQLite 文件（tmp_path fixture） |
| 缓存行为 | TTLCache 封装 | 进程内，无需外部依赖 |
| 反馈流程 | Feedback Task | 临时 SQLite + asyncio 队列 |
| 向量检索 | sqlite-vec + FTS5 | 临时 SQLite（加载 vec 扩展） |
| 完整 Pipeline | Orchestrator | mock LLM + 临时 SQLite |

### 9.4 E2E 测试场景
- 基本查询：stdio 启动服务 -> 调用 rsi_query -> 验证响应格式
- 反馈流程：调用 rsi_query -> 使用 feedback_token 调用 rsi_feedback -> 验证日志/策略更新
- 熔断恢复：mock LLM 连续故障 -> 验证熔断 OPEN 与降级 -> 恢复后验证正常
- 冷启动：全新项目目录启动 -> 验证自动建库/迁移/bootstrap 提示

## 10. 集成方案设计

> **核心原则**：RSI Boot 不是 SDK/库，也不是网关服务，而是一个 **MCP Server 进程**——与 Superpowers 同类的本地开发工具。用户不通过代码 import 来使用它，而是由 IDE 通过 MCP 协议按项目拉起它。

### 10.1 接入形态全景

仅 **一种接入形态**：本地 stdio 模式，安装即用。

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           应用层 (IDE / CLI)                              │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌────────────────────┐  │
│  │ VS Code   │  │ JetBrains │  │ Cursor    │  │ 终端 (CLI)          │  │
│  │ MCP 插件  │  │ MCP 插件   │  │ MCP 内置  │  │ rsi query "..."     │  │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────────┬─────────┘  │
└────────┼──────────────┼──────────────┼────────────────────┼────────────┘
         │              │              │                    │
         ▼              ▼              ▼                    ▼
   ┌──────────────────────────────────────────────────────────────┐
   │              MCP stdio（本地进程管道，无网络监听）              │
   │                                                              │
   │   IDE 按项目拉起 RSI Boot 进程（cwd = 项目根目录），           │
   │   数据落在该项目 `.rsi/` 目录，随 IDE 会话启停                  │
   └──────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                     LLM 提供商 (HTTPS)                        │
   │          OpenAI / Anthropic / DeepSeek / ...                 │
   └──────────────────────────────────────────────────────────────┘
```

---

### 10.2 运行模式

| 项目 | 说明 |
|------|------|
| 适用场景 | 个人开发全流程（编码/测试/文档/评审） |
| 传输协议 | MCP stdio（本地进程间管道，无网络端口） |
| 认证 | 无（信任本地进程边界，§7.2） |
| 存储后端 | SQLite 单文件（`.rsi/rsi.db`）+ sqlite-vec，零外部服务 |
| 启动方式 | 通常由 IDE MCP 配置自动拉起；手动调试用 `rsi start` |
| 生命周期 | 随 IDE 项目窗口启停，用完即弃 |
| 数据位置 | 项目目录下 `.rsi/` 文件夹 |

**IDE 配置示例**（VS Code `.vscode/mcp.json`）：

```json
{
  "servers": {
    "rsi-boot": {
      "type": "stdio",
      "command": "rsi",
      "args": ["start", "--stdio"]
    }
  }
}
```

> 安装即用的关键：`pip install rsi-boot` 后在 IDE 注册一次 MCP Server 即可；每个项目窗口自动获得独立实例与独立数据目录，无需任何服务端部署。

---

### 10.3 项目配置机制

#### 10.3.1 项目配置文件 `rsi-boot.yaml`

每个项目通过在根目录放置 `rsi-boot.yaml` 来声明 RSI 配置（可选——无配置文件也能跑，走默认配置）。进程启动时自动发现该文件。

```yaml
# rsi-boot.yaml - 项目根目录下的 RSI 配置文件（全部可选）
project:
  id: "my-web-app"
  name: "My Web Application"
  language: "python"
  framework: "django"

knowledge:
  sources:
    - path: "./docs/*.md"
    - path: "./docs/adr/**/*.md"
    - path: "./CONTRIBUTING.md"
  auto_index: true

role:
  default: "developer"        # 本人在此项目的主要角色；单次请求可覆盖

strategy:
  code_review:
    model: "gpt-4o"
    template: "code_review"
  test_gen:
    model: "gpt-4o-mini"        # Phase 4 接入多厂商后可改为 deepseek-coder 等
    template: "test_gen"
  prd_gen:
    model: "gpt-4o"             # Phase 4 接入多厂商后可改为 claude-sonnet 等
    template: "prd_gen"

budget:
  daily_token_cap: 500000       # 可选，默认不开启（§6.2）
```

> LLM API Key 禁止写入此文件（可能入库），一律走环境变量（§8.2）。

#### 10.3.2 项目自动发现

进程启动时按以下路径逐级查找项目配置：

```
查找顺序（优先级从高到低）：
1. 命令行参数: --project-root /path/to/project
2. 当前工作目录: cwd 目录下的 rsi-boot.yaml（IDE 拉起时 cwd = 项目根目录，绝大多数情况命中此层）
3. Git 仓库根: git rev-parse --show-toplevel 下的 rsi-boot.yaml
4. 父目录递归: 递归向上查找 rsi-boot.yaml
5. 默认配置: 使用包内 config/default.yaml
```

**项目初始化流程**：

```
进程启动 (stdio 连接建立)
       |
       v
  +----------------+
  | 查找项目配置     |<---- 按优先级逐级查找 rsi-boot.yaml
  +-------+--------+
          |
          v
  +----------------+
  | 加载合并配置     |<---- §2.5 配置链合并
  +-------+--------+
          |
          v
  +----------------+
  | 初始化数据目录   |<---- .rsi/ 不存在则创建；建库 + 执行迁移（§2.2）
  +-------+--------+
          |
          v
  +----------------+
  | 加载运行时状态   |<---- 画像缓存预热、策略加载、熔断器冷启动
  +-------+--------+
          |
          v
  +----------------+
  | 进入服务循环     |<---- tools/list + tools/call
  +----------------+
```

#### 10.3.3 配置优先级链

```
低                                                              高
+-------------+-------------------+----------------+-------------+
default.yaml    ~/.rsi/config.yaml  rsi-boot.yaml    请求参数覆盖
(包内置基线)    (个人全局偏好)      (项目配置)       (运行时动态)
```

后层配置**层叠覆盖**前层。本链路与 §2.5 配置继承链为同一套规则，config_merger 以此为准。

---

### 10.4 多项目支持

**一个进程实例服务一个项目**（IDE 按项目窗口拉起，cwd 即项目根目录）：

- 每个项目独立 `.rsi/` 数据目录：知识库、项目画像、策略配置、日志按项目隔离
- 切换项目 = 在 IDE 打开另一个项目窗口，自动获得独立实例，无需任何手动操作
- **全局画像跨项目共享**：`user_profiles` 中 `project_id=''` 的全局画像记录跨项目的通用偏好（如「偏好 pytest 风格」），各项目实例读写同一份——通过 `~/.rsi/global.db` 存放全局画像与跨项目统计，项目库只存项目域数据

> 由此数据落点为两层：`~/.rsi/global.db`（全局画像/跨项目用量统计）与 `<project>/.rsi/rsi.db`（项目知识/日志/策略）。§2.2 表结构两者同构，全局库仅含 user_profiles 与统计视图所需表。

---

### 10.5 数据目录约定

```
<project>/.rsi/
├── rsi.db            # SQLite 主库（含 sqlite-vec 向量与 FTS5 索引）
├── rsi.db-wal/-shm   # WAL 伴随文件
├── archive/          # 过期日志归档（YYYYMM.jsonl，§8.3）
└── cache/            # 可重建的临时产物（嵌入缓存等），可随时清空

~/.rsi/
├── config.yaml       # 个人全局配置（§2.5）
└── global.db         # 全局画像与跨项目统计（§10.4）
```

**`.gitignore` 建议**（`rsi bootstrap` 自动写入，§10.9）：

```gitignore
# RSI Boot 本地数据
.rsi/
```

---

### 10.6 与 CI/CD 集成

RSI Boot 可在 CI 流水线中用于自动化任务（无状态、用完即弃）：

```yaml
# .github/workflows/rsi-ci.yml 示例
jobs:
  rsi-code-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install RSI Boot
        run: pip install rsi-boot
      - name: Run code review on PR diff
        run: git diff main...HEAD | rsi query "请审查以下代码变更" --input=-
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

CI 环境无 `.rsi/` 历史数据，自动走冷启动路径（仅本次 diff 上下文），不依赖任何外部服务。

---

### 10.7 设计取舍说明

**为什么只有本地模式**：RSI Boot 的定位是个人开发工具（与 Superpowers 同类），核心价值是「安装即用、零运维」。团队共享模式（HTTP 服务 + 认证 + 多租户 + 集中存储）会引入部署与运维成本，违背工具定位，刻意不做。若未来出现真实的团队共享需求（多人知识库共建），再作为独立项目评估，不在本工具范围内。

由此移除的能力及替代方案：

| 移除项 | 替代方案 |
|--------|----------|
| 认证/权限体系 | 本地进程信任边界（§7.2） |
| 限流 | 个人使用无滥用场景；成本意识由 §6.2 每日上限承担 |
| 多租户隔离 | 每项目独立 `.rsi/` 目录物理隔离（§10.4） |
| 集中部署/运维 | pip 安装 + IDE 拉起，零运维 |

---

### 10.8 使用流程完整示例

#### 场景：开发者在已有项目中启用 RSI

```bash
# Step 1: 安装
pip install rsi-boot

# Step 2: 进入项目目录
cd my-django-project

# Step 3: 初始化项目配置（自动检测语言、框架、生成 rsi-boot.yaml）
rsi init

# Step 4: 配置 LLM API Key（环境变量，§8.2）
export OPENAI_API_KEY="sk-xxx"

# Step 5: 自学习（可选但强烈推荐，§10.9）
rsi bootstrap

# Step 6: 验证
rsi query "这个项目的架构是什么？"

# Step 7: 配置 IDE（一次性）
# VS Code -> 安装 MCP 插件 -> 添加上述 stdio MCP Server 配置
```

#### 场景：测试人员使用 RSI 生成测试用例

```bash
rsi query "为 user_service.py 的 create_user 方法编写 pytest 测试用例" \
  --role test \
  --context.language python \
  --context.file_path src/services/user_service.py

# 或在 IDE 中选中代码段，右键 -> RSI -> 生成测试
```

#### 场景：产品经理使用 RSI 生成需求文档

```bash
rsi query "基于以下功能描述生成 PRD 草稿：..." \
  --role pm \
  --context.language zh
```

### 10.9 项目自学习与初始化（Project Bootstrap & Auto-Learn）

> **场景**：用户的项目可能有多年积累的代码、文档、对话记录、Git 历史、配置文件等多维信号源。
> 系统不是等着用户「导入」数据，而是主动扫描项目全貌，自动从中学习：代码结构、文档体系、
> 团队规范、沟通模式、技术栈偏好——让 AI 在第一次启动时就对项目有「第一印象」。
> 对于已有存量项目，**强烈建议执行此步骤**，它能让工具在第一天就具备项目认知。

#### 10.9.1 引导命令 `rsi bootstrap`

`rsi bootstrap` 是项目自学习的统一入口，它会自动扫描当前项目，发现所有可学习的信号源并消化：

```bash
# 一键自学习（扫描项目全貌：文档 + 代码 + Git + 配置 + 对话上下文）
rsi bootstrap

# 仅学习特定维度
rsi bootstrap --scope=docs          # 只扫描文档
rsi bootstrap --scope=code          # 只分析代码结构
rsi bootstrap --scope=git           # 只分析 Git 历史
rsi bootstrap --scope=conversation  # 只学习对话上下文（如 .cursor/conversations/）
                                    # 注意：对话上下文涉及个人隐私，需显式同意（见下）
rsi bootstrap --scope=config        # 只检测配置和规范

# 对话上下文扫描需用户显式同意：交互式确认提示，或显式携带 --consent 标志；
# 未同意时自动跳过 conversation 维度，其余维度不受影响
rsi bootstrap --scope=conversation --consent

# 预览扫描计划但不实际执行
rsi bootstrap --dry-run

# 指定项目根目录（默认当前目录）
rsi bootstrap --project-root=/path/to/repo

# 自学习完成后自动启动服务
rsi bootstrap --then-start

# 限制扫描深度（大项目用）
rsi bootstrap --max-commits=500 --max-file-size=1MB
```

> `rsi import` 作为向下兼容的别名保留，实际行为等同于 `rsi bootstrap --scope=docs,git,config`。

#### 10.9.2 自学习数据来源矩阵

系统自动从以下信号源中汲取信息，无需用户标注数据来源：

| 信号源 | 自动发现方式 | 学习产出 | 首次扫描耗时 |
|--------|------------|---------|------------|
| **项目文档**（README、docs/、ADR、Wiki） | 扫描常见文档路径 + 文件名模式 | 知识库条目（向量化） | 2-10s |
| **代码结构**（函数/类/模块/API 路由） | AST 静态分析提取代码骨架 | 项目架构知识、函数签名映射 | 5-30s（项目大小） |
| **Git 历史**（commit message、分支模式、文件归属） | `git log --all` + 统计分析 | 编码风格画像、贡献领域、团队协作模式 | 5-15s |
| **对话上下文**（IDE 聊天记录、代码审查评论） | 扫描 `.cursor/`、`.vscode/`、`CHANGELOG*` 等（**需用户显式同意**） | 常见问题模式、历史决策记录；提取的模式默认经人工确认后入项目知识库，个人可标记 `private` 不共享 | 2-8s |
| **项目配置**（package.json、pyproject.toml、pom.xml、Makefile） | 文件存在性检测 + 解析 | 技术栈识别、构建工具、测试框架偏好 | 0.5-2s |
| **测试文件**（test_*.py、*.spec.ts、*_test.go、测试配置） | 按语言约定目录和命名模式扫描 | 测试风格、覆盖率习惯、断言风格画像 | 3-10s |
| **规范文件**（.editorconfig、eslintrc、tsconfig、prettierrc、Dockerfile） | 规则文件检测 + 解析 | 代码规范、命名约定、格式规则 | 0.5-2s |
| **PR/Issue 模板**（.github/*.md、CONTRIBUTING） | 路径模式匹配 | 团队协作规范、角色模板参考 | 1-3s |
| **构建/CI 配置**（.github/workflows/*、Jenkinsfile、.gitlab-ci.yml） | 路径模式匹配 + 解析 | CI 流程知识、部署架构模式 | 1-5s |

#### 10.9.3 自学习流程

`rsi bootstrap` 的执行流程是自动发现驱动的，而非用户指令驱动的：

```
rsi bootstrap
  |
  v
+----------------------------+
| 阶段一：信号发现           |---- 扫描项目目录树，检测存在哪些信号源
| 自动检测 9 类信号是否存在  |---- 生成扫描计划 (显示给用户确认或 --dry-run)
+------------+---------------+
             |
             v
+----------------------------+
| 阶段二：并行采集           |---- 各信号源独立并行采集
| ├─ 文档爬取 + 切片         |---- docs/、*.md、README → 切块/向量化
| ├─ 代码 AST 分析           |---- 提取模块/类/函数/路由/API 端点
| ├─ Git 历史拉取 + 分析     |---- commit log → 编码风格/贡献画像
| ├─ 对话上下文扫描          |---- IDE 聊天记录 → 常见问题/决策记录
| └─ 配置/规范解析           |---- 解析各类配置文件 → 技术栈/规则集
+------------+---------------+
             |
             v
+----------------------------+
| 阶段三：关联推理           |---- 交叉关联不同信号源
| ├─ 文档 ↔ 代码 ↔ 对话      |---- 理解「这个文档对应哪个模块」
| ├─ 测试 ↔ 代码             |---- 知道「这个测试测试哪个函数」
| └─ Commit ↔ 文件            |---- 知道「谁主要负责哪块代码」
+------------+---------------+
             |
             v
+----------------------------+
| 阶段四：知识写入           |---- 将学习成果写入知识库和画像
| ├─ 知识库条目（向量化）    |
| ├─ 用户画像（初始填充）    |
| ├─ 项目画像（技术栈/架构） |
| └─ 角色模板建议            |
+------------+---------------+
             |
             v
+----------------------------+
| 阶段五：报告输出           |---- 打印学习摘要
| 发现 > 学习 > 关联 > 写入  |---- 报告每类信号的学习成果
+----------------------------+
```

**关键差异**：与传统的「导入」不同，自学习有三个核心特点：
1. **自动发现**：系统自行判断项目有哪些信号源，用户无需指定
2. **关联推理**：不同信号源之间的交叉理解（如「这个文档讲的是哪个模块」）
3. **增量吸收**：项目变化时自动感知并增量学习，不需要全量重扫

#### 10.9.4 初始画像生成

自学习引擎从多维度信号中综合推断画像，而非单一来源：

```yaml
# 初始画像示例（由 rsi bootstrap 自动生成）
user_id: "alice@example.com"
role: "developer"              # 优先从 rsi-boot.yaml 读取，否则从文件归属推断
preferences:
  detail_level: "normal"
  response_lang: "zh"
  code_style: "snake_case"    # 综合：代码扫描 + Git log + .editorconfig
  role_specific:              # 角色特定偏好命名空间（见 §2.1 UserPreferences）
    language: "python"
    framework: "django"
    test_framework: "pytest"
    commit_style: "conventional" # Git log commit message 格式分析
expertise:
  - "django"                   # 从文件修改历史 + 代码结构推断
  - "rest-api"
  - "postgresql"
project_insights:               # 新增：项目级别的综合认知
  architecture: "django-rest-framework + PostgreSQL"
  build_system: "poetry"
  ci_pipeline: "github-actions"
  doc_quality: "good"          # docs/ 覆盖率 + README 完善度
  test_culture: "strong"       # 测试文件占比 + commit 中 test 比例
history_summary: "根据代码结构分析，项目采用 Django REST Framework 架构；
从 Git 历史看，主要贡献领域为 API 开发和数据库模型设计，
test 文件占比约 35%，测试文化较好；
docs/ 目录包含了完整的 API 文档和部署指南。
团队 commit message 遵循 conventional commits 规范。"
```

#### 10.9.5 智能扫描策略

系统根据项目特征自动选择最优扫描策略：

| 策略 | 触发条件 | 行为 |
|------|---------|------|
| **全量学习** | 首次 `rsi bootstrap`、`--force` | 全面扫描所有信号源，写入完整知识库 |
| **增量学习** | 已存在 `.rsi/manifest.json` | 对比文件哈希 + mtime，仅处理新增/变更的信号 |
| **快速学习** | 项目 > 5000 文件 | 仅扫描根目录和常见路径，不深入 `vendor/` 等第三方目录 |
| **渐进学习** | 指定 `--scope` | 只学习特定维度，不干扰已有知识 |
| **静默学习** | `rsi start --watch` 启动的文件监听模式 | 在进程运行期间监听文件变化，后台自动增量学习 |

**增量学习的实现原理**：
1. 在 `.rsi/manifest.json` 中维护每个信号源的「学习指纹」（文件哈希 + 时间戳 + 信号类型）
2. 增量扫描时，只处理指纹变化的信号
3. 删除的信号从知识库中标记为 `archived`
4. 对话上下文类信号（如聊天记录）每次增量扫描追加新内容，不做全量重扫

#### 10.9.6 支持的扫描格式

系统自动检测并处理以下格式，无需用户配置：

| 信号类别 | 被检测文件 / 路径模式 | 处理方式 |
|---------|---------------------|---------|
| **文档** | `**/*.md`、`**/*.rst`、`**/*.txt`、`**/*.adoc`、`**/README*`、`**/CONTRIBUTING*`、`docs/**/*`、`wiki/**/*` | 全文 → 按标题/段落切片 → 向量化 |
| **代码骨架** | `**/*.py`、`**/*.js`、`**/*.ts`、`**/*.java`、`**/*.go`、`**/*.rs`、`**/*.cs`、`**/*.cpp` | AST 提取：模块/类/函数/API 路由签名 + docstring |
| **配置** | `**/pyproject.toml`、`**/package.json`、`**/pom.xml`、`**/build.gradle*`、`**/Cargo.toml`、`**/go.mod`、`**/*.yaml`、`**/*.yml`、`**/*.toml`、`**/*.json`、`**/*.ini`、`**/*.cfg` | 解析结构化数据 → 提取关键键值对 → 作为结构化知识 |
| **规范** | `**/.editorconfig`、`**/.eslintrc*`、`**/.prettierrc*`、`**/tsconfig*`、`**/Dockerfile*`、`**/Makefile*`、`**/docker-compose*` | 识别规则集 → 生成项目规范摘要 |
| **Git** | `.git/`（自动检测） | `git log --all --format=...` → 风格/贡献/协作模式分析 |
| **对话上下文** | `.cursor/**/*`、`.vscode/**/*.json`、`**/CHANGELOG*`、`**/HISTORY*`、`**/RELEASE_NOTES*`、`**/AUTHORS*` | 提取历史对话模式 → 常见问题聚类 → FAQ 知识 |
| **测试** | `**/test_*.py`、`**/*.test.js`、`**/*.spec.ts`、`**/*_test.go`、`**/__tests__/**`、`**/tests/**` | 统计测试框架/断言风格/覆盖率倾向 |
| **CI/CD** | `.github/workflows/**`、`**/Jenkinsfile*`、`**/.gitlab-ci.yml`、`**/.circleci/**` | 解析构建步骤 → 部署架构知识 |
| **模板** | `.github/ISSUE_TEMPLATE/**`、`.github/PULL_REQUEST_TEMPLATE/**`、`**/CONTRIBUTING*` | 提取模板结构 → 角色提示词参考 |

> **设计原则**：不扫描二进制文件、编译产物、依赖目录（`node_modules/`、`vendor/`、`__pycache__/`、`.git/objects/`）。
> 代码文件仅提取骨架签名（模块名、类名、函数签名、路由定义），不导入完整源码，避免知识库膨胀和许可证风险。
> 对话上下文仅提取问题和模式，不导入个人信息。

#### 10.9.7 数据验证规则

每条学习到的数据写入前经过以下验证：

| 检查项 | 规则 | 处理方式 |
|--------|------|---------|
| **信息量** | 切片 < 50 token → 跳过（噪声） | 记录日志，跳过 |
| **上限** | 切片 > 4000 token → 自动拆分 | 按语义边界拆分为多条 |
| **语言检测** | `langdetect` 识别文档语言 | 标注 metadata.lang，供检索排序 |
| **去重** | SHA256(content) 与现有知识库比对 | 命中则跳过 |
| **敏感信息** | 正则匹配 API Key / Token / Password / 内网 IP / 密钥文件（模式表见 §8.1，同源复用） | 匹配命中 → 条目标记 `risk: true`，默认不写入（需 `--allow-sensitive` 豁免） |
| **隐私同意** | 对话上下文类信号需用户显式同意（交互确认或 `--consent`） | 未同意 → 跳过 conversation 维度 |
| **编码检测** | UTF-8 / GBK / Latin-1 | 自动转码尝试，失败则跳过 |
| **格式完整性** | YAML/JSON/XML/Toml 解析验证 | 解析失败 → 记录错误，跳过 |
| **Git 可用性** | 验证 `git log` 可执行和仓库有效 | Git 不可用 → 跳过该维度，不影响其他 |

**严格模式**：`rsi bootstrap --strict` 下，任何验证失败中断流程并输出详细报告。默认模式下行，单条失败不影响其他。

#### 10.9.8 跨信号源关联推理

这是自学习区别于简单导入的核心能力——系统会交叉关联不同信号源，形成深层理解：

```
┌─────────────────────────────────────────────────────┐
│  关联推理引擎 (Cross-Signal Correlation Engine)     │
│                                                     │
│  文档 ↔ 代码：  README 中提到的功能 <-> 代码模块名  │
│                → 理解「这个文档讲的是哪个模块」      │
│                                                     │
│  测试 ↔ 代码：  test_user.py <-> user/models.py     │
│                → 知道「这个测试覆盖哪块逻辑」        │
│                                                     │
│  Commit ↔ 文件： "feat: add user auth" <-> auth.py  │
│                → 理解「修改说明对应哪些文件」        │
│                                                     │
│  对话 ↔ 决策：  Chat 记录 "我们为什么选 DRF" <-> ADR │
│                → 提取架构决策上下文                  │
│                                                     │
│  CI ↔ 测试：    test 步骤 <-> 测试文件               │
│                → 知道「CI 中怎么跑测试」             │
└─────────────────────────────────────────────────────┘
```

**关联方式**：
- 命名关联：文件名 + 路径层次匹配（`test_user.py` ↔ `user.py`）
- 文本关联：文档中出现的关键词与代码模块名的 Jaccard 相似度
- 时间关联：相近时间戳的 commit message 和文件变更关联
- 结构关联：API 路由定义文档与代码中的路由装饰器/注册器关联

#### 10.9.9 错误处理与部分恢复

自学习过程中可能遇到多种错误，处理策略如下：

**阶段一：信号发现错误**
- 权限不足的目录 → 跳过并记录警告
- 不是 Git 仓库 → 跳过 Git 维度，其他正常执行
- 编码无法识别 → 尝试 UTF-8 → GBK → Latin-1，全部失败则跳过

**阶段二：采集错误**
- 文档切片失败 → 跳过该文件，继续下一个
- AST 解析失败（语法错误文件） → 跳过该文件，记录文件路径
- 向量化服务不可用 → 降级为纯文本存储，待恢复后异步重试
- Git 拉取中断 → 自动重试 3 次（指数退避）
- Worker 异常退出 → 主进程捕获，重启 Worker 继续

**阶段三：写入错误**
- 数据库写入失败 → 批量回退，2s 后重试，最多 3 次
- 部分写入成功 → 记录已写入条目 ID，失败写入 `bootstrap_error.log`
- 断点续学：每处理 50 个条目写入一次 checkpoint，中断后可恢复

#### 10.9.10 去重与增量更新

`rsi bootstrap` 对同一项目多次运行具有**幂等性**：

| 场景 | 默认行为 | 覆盖选项 |
|------|---------|---------|
| 同一信号未变 | 跳过（SHA256 哈希比较） | `--force` 强制重学 |
| 同一信号已变 | 增量更新：旧条目标记过期，新条目写入 | `--keep-history` 保留旧版本 |
| 信号源被删除 | 对应知识条目标记 `archived`，不删除 | `--purge` 彻底删除 |
| 新增信号源 | 自动发现并学习 | — |
| 对话上下文追加 | 增量追加，不做全量重扫 | `--force` 重新全量学习对话 |

#### 10.9.11 不同成熟度项目场景

`rsi bootstrap` 根据项目信号源的丰裕度自动调整行为：

**场景 A：白板项目（无 Git、无文档、几个文件）**
```bash
rsi init && rsi start
# bootstrap 会提示：项目信号较少，建议在日常使用中积累
# 系统仍会从已有文件中尽力学习
```
- 产出物有限，但系统能识别技术栈（如果有 package.json 等）
- 后续在日常使用中通过学习引擎逐步构建画像

**场景 B：成长项目（1-2 年，有文档、有测试）**
```bash
rsi bootstrap --then-start
```
- 文档和测试丰富，知识库密度高
- Git 历史能提取稳定的编码风格和团队协作模式
- 关联推理效果好：文档 ↔ 代码 ↔ 测试 之间能建立联系

**场景 C：成熟项目（3+ 年，多信号源丰富）**
```bash
rsi bootstrap --max-file-size=2MB --then-start
```
- 信号源丰富，但部分文档可能过时 → 报告标注最后修改时间，过时文档检索权重降低
- Git 历史能分析多个贡献者的风格差异 → 生团队画像摘要
- 建议先 `--dry-run` 预览信号发现结果

**场景 D：有对话记录的项目（使用过 Cursor/Codex 等 AI 工具的团队）**
```bash
rsi bootstrap --scope=conversation,docs,git
```
- 从历史对话中提取常见问题模式 → 生成 FAQ 知识
- 从对话中了解团队对架构的讨论 → 补充决策上下文
- 特别注意：对话类信号中包含的意见分歧和最终决策，是最有价值的知识之一


**场景 E：存量项目（3+ 年，多贡献者，大量历史数据和行为习惯）**
```bash
rsi bootstrap --max-file-size=2MB --dry-run
# 先预览信号发现结果，确认范围后再执行
rsi bootstrap --then-start
```
- **适用场景**：项目已开发运行 3 年以上，数十万行代码、数千次提交、多人协作、大量文档和技术决策记录
- **自动发现**：系统会自动扫描所有历史信号源，无需用户指定路径或类型
- **代码积累分析**：从多年代码变迁中识别稳定的架构模式、接口设计规范和 API 演变轨迹
- **Git 深度挖掘**：分析多年提交历史，识别模块负责人、Review 习惯、编码风格一致性
- **文档沉淀处理**：大量历史文档可能包含过时内容 → 报告标注最后修改时间，过时文档检索权重自动降低
- **行为习惯学习**：从历史对话记录和代码审查评论中提取团队的隐性规范（如命名偏好、注释风格、测试覆盖习惯）
- **跨贡献者画像**：分析多个开发者的协作模式，生成团队级画像摘要 + 各成员子画像
- **建议预处理**：首次对存量项目执行时，建议先 `--dry-run` 预览信号发现结果，再正式执行
- **特别注意**：存量项目中的废弃代码和历史遗留文档会被自动识别并降低权重，不会污染知识库

#### 10.9.12 学习报告

学习完成后输出结构化报告：

```
┌─────────────────────────────────────────┐
│  RSI Bootstrap Summary                   │
├─────────────────────────────────────────┤
│  项目             │  my-project          │
│  扫描模式         │  full                │
│  耗时             │  18.2s               │
├─────────────────────────────────────────┤
│  信号发现结果                            │
│  ✅ 文档          │  47 个文件 → 213 条  │
│  ✅ 代码骨架      │  38 个模块, 215 函数  │
│  ✅ Git 历史      │  500 commits, 5 人   │
│  ✅ 对话上下文    │  3 个会话文件 → 12 模式 │
│  ✅ 配置/规范     │  8 个文件 → 技术栈识别 │
│  ✅ 测试          │  pytest (35% 覆盖率)  │
│  ⚠  CI/CD        │  未发现 CI 配置文件   │
├─────────────────────────────────────────┤
│  关联推理成果                            │
│  ✅ 文档 ↔ 代码   │  15 个关联对建立成功  │
│  ✅ 测试 ↔ 代码   │  28 个关联对建立成功  │
│  ⚠ 对话 ↔ 决策    │  3 个候选需人工确认  │
├─────────────────────────────────────────┤
│  画像填充率       │  13/15 (87%)         │
├─────────────────────────────────────────┤
│  建议角色模板                            │
│  Developer      │  code_review.prompt   │
│  Test Engineer  │  test_gen.prompt      │
│  Product Mgr    │  prd_gen.prompt       │
├─────────────────────────────────────────┤
│  警告 (2)                                │
│  ⚠  1 个文件编码无法识别                  │
│  ℹ  已写入 bootstrap_error.log          │
└─────────────────────────────────────────┘
```

报告同时以 JSON 格式写入 `.rsi/bootstrap_report.json`，可供 CI 解析。

#### 10.9.13 持续学习机制

`rsi bootstrap` 解决的是冷启动问题——让 RSI Boot 第一天就了解项目。
但它只是开始，持续学习才是真正的进化引擎：

```
──── 时间线 ────

rsi bootstrap    →  日常使用 (rsi start)    →  项目演进
    │                      │                       │
    ▼                      ▼                       ▼
 初始知识库          学习引擎持续运行         重新 bootstrap
 初始画像           ├─ 隐式反馈捕获          (每季度或大版本)
 关联索引           ├─ 策略 A/B 调优         ├─ 检测架构变化
                    ├─ 知识条目更新           ├─ 重新关联推理
                    └─ 画像渐进修正           └─ 更新知识索引

    冷启动阶段           持续优化阶段              重构适应阶段
```

| 学习模式 | 触发条件 | 范围 | 对知识库影响 |
|---------|---------|------|------------|
| **一次性自学习** | `rsi bootstrap` | 全量或指定 scope | 初始填充 |
| **文件监听学习** | `rsi start --watch` + 文件变化 | 仅变化文件 | 增量更新 |
| **隐式反馈学习** | 用户交互（采纳/修改/复制） | 单轮交互 | 策略调优 + 质量评分 |
| **显式反馈学习** | `rsi_feedback` | 单轮交互 | 画像修正 + 知识提取 |
| **离线重学** | 定时任务（Cron/CI） | 全量增量 | 知识库刷新 + 过期清理 |

> **最佳实践**：首次 `rsi bootstrap` 解决冷启动，日常依靠 `rsi start --watch` 和反馈闭环持续进化，
> 每季度或大版本重构后执行一次 `rsi bootstrap --force` 刷新项目认知。

---

## 11. 目录结构

```
rsi-boot/
+-- pyproject.toml              # 项目依赖与构建配置（pip 安装入口）
+-- README.md                   # 项目说明
+-- config/                     # 包内配置文件
|   +-- default.yaml            # 默认配置（基线）
|   +-- models.yaml             # 模型单价表（§6.1）
|   +-- roles/                  # 角色意图注册（developer.yaml / test.yaml / pm.yaml）
|   +-- role_templates/         # 角色提示词模板（test_gen.jinja2 / prd_gen.jinja2 等）
|   +-- quality_rubric.yaml     # 角色质量权重（见 §3.5）
|   +-- response_format.yaml    # 角色格式化规则（见 §3.7）
|   +-- skills/                 # 文件型技能槽（§3.9，每技能一个目录，内含 SKILL.md + 可选脚本/资源）
|   +-- templates/              # 提示词模板
|       +-- base.jinja2
|       +-- code_review.jinja2
|       +-- code_gen.jinja2
|       +-- debug.jinja2
|       +-- explain.jinja2
|       +-- general_assist.jinja2
+-- migrations/                 # SQLite 迁移脚本（PRAGMA user_version 顺序执行，§2.2）
|   +-- 001_init.sql
+-- src/                        # 核心源码
|   +-- __init__.py
|   +-- __main__.py             # CLI 入口（rsi）
|   +-- core/                   # 核心模块
|   |   +-- models.py           # Pydantic 数据模型
|   |   +-- config_merger.py    # 配置合并
|   |   +-- masking.py          # 数据脱敏
|   |   +-- exceptions.py       # 自定义异常
|   +-- config/                 # 配置管理
|   |   +-- loader.py           # YAML + 环境变量加载
|   |   +-- settings.py         # Pydantic Settings
|   +-- preprocessor/           # 预处理
|   |   +-- handler.py          # Pipeline Handler
|   |   +-- intent_classifier.py # 意图识别
|   +-- strategy/               # 策略引擎
|   |   +-- engine.py           # 策略选择
|   |   +-- thompson_sampler.py # Thompson Sampling
|   |   +-- prompt_renderer.py  # 提示词渲染器
|   +-- model/                  # 模型适配
|   |   +-- adapter.py          # 统一调用接口
|   |   +-- registry.py         # 模型注册中心
|   |   +-- providers/          # 各厂商适配
|   |       +-- base.py
|   |       +-- openai.py
|   +-- orchestrator/           # 编排器
|   |   +-- pipeline.py         # Pipeline 定义
|   |   +-- handlers/           # 各阶段 Handler
|   |       +-- preprocess.py
|   |       +-- strategy.py
|   |       +-- model.py
|   |       +-- postprocess.py
|   |       +-- log.py
|   +-- feedback/               # 反馈处理
|   |   +-- task.py             # 进程内异步任务（asyncio.Queue 消费，§4.2）
|   |   +-- token_generator.py  # feedback_token 生成
|   +-- learning/               # 学习闭环 (Phase 3)
|   |   +-- knowledge_extractor.py # 知识提取（§4.3）
|   |   +-- feedback_analyzer.py  # 反馈统计
|   |   +-- failure_miner.py    # 失败模式挖掘（§3.9）
|   |   +-- proposal_engine.py  # Harness 提案生成/回归验证/版本化（§3.9）
|   +-- knowledge/              # 知识管理 (Phase 2)
|   |   +-- retriever.py        # 混合检索（FTS5 + sqlite-vec + RRF）
|   |   +-- embedding.py        # 向量化
|   +-- services/               # 业务服务层
|   |   +-- log_service.py
|   |   +-- profile_service.py
|   |   +-- knowledge_service.py
|   +-- data/                   # 数据访问层
|   |   +-- proxy.py            # DataServiceProxy
|   |   +-- sqlite.py           # SQLite 客户端（aiosqlite，WAL）
|   |   +-- vec.py              # sqlite-vec 扩展加载与向量操作
|   |   +-- migrate.py          # PRAGMA user_version 迁移执行器
|   +-- api/                    # MCP 层
|   |   +-- mcp_server.py       # MCP Server 实现（stdio）
|   |   +-- tools/              # MCP 工具实现
|   |       +-- query_tool.py   # rsi_query
|   |       +-- feedback_tool.py # rsi_feedback
|   |       +-- knowledge_tool.py # rsi_knowledge_* (Phase 2)
|   |       +-- stats_tool.py   # rsi_stats（用量统计，P3）
|   +-- scheduler/              # 进程内定时任务（§4.3）
|   |   +-- tasks.py            # 离线任务定义
|   |   +-- manager.py          # asyncio 调度器
|   +-- scanner/                # 项目自学习 rsi bootstrap（M2，见 §10.9）
|   |   +-- signal_discovery.py # 信号发现引擎
|   |   +-- document_scanner.py # 文档扫描
|   |   +-- code_scanner.py     # 代码骨架 AST 分析
|   |   +-- conversation_scanner.py # 对话上下文（需用户同意）
|   |   +-- git_analyzer.py     # Git 历史分析
|   |   +-- config_scanner.py   # 配置/规范解析
|   |   +-- correlation_engine.py # 跨信号源关联推理
|   |   +-- profile_generator.py  # 初始画像生成
|   |   +-- incremental.py      # 增量学习与文件监听
|   |   +-- validator.py        # 数据验证
|   |   +-- error_handler.py    # 错误处理与断点续学
|   |   +-- report.py           # 学习报告
|   +-- cli/                    # CLI 命令
|   |   +-- bootstrap_command.py # rsi bootstrap
|   +-- common/                 # 工具函数
|       +-- circuit_breaker.py  # 熔断器（进程内，§5.1）
|       +-- retry.py            # 重试工具
|       +-- logger.py           # 日志配置
+-- tests/                      # 测试
|   +-- conftest.py             # 共享 fixtures（tmp_path SQLite 等）
|   +-- unit/                   # 单元测试
|   |   +-- test_preprocessor.py
|   |   +-- test_strategy_engine.py
|   |   +-- test_model_adapter.py
|   |   +-- test_orchestrator.py
|   |   +-- test_config_merger.py
|   |   +-- test_circuit_breaker.py
|   |   +-- test_masking.py
|   |   +-- test_migrate.py
|   +-- integration/            # 集成测试
|   |   +-- test_log_service.py
|   |   +-- test_feedback_task.py
|   |   +-- test_retriever.py
|   +-- e2e/                    # 端到端测试（stdio 子进程）
|       +-- test_pipeline.py
+-- schemas/                    # JSON Schema
|   +-- rsi_query.schema.json
|   +-- rsi_feedback.schema.json
+-- docs/                       # 文档
|   +-- prd/
|   +-- specs/
|   +-- plans/
+-- scripts/                    # 工具脚本
    +-- seed_knowledge.py
    +-- export_data.py
```

> **文档信息**
> - 版本：v2.6
> - 最后更新：2026-09-10
> - 下一阶段：Phase 1-M1 实现
