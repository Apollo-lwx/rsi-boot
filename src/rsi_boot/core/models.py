"""核心 Pydantic 模型（Spec §2.1，单一事实来源）。

SQLite 适配约定（§2.2）：UUID 存 TEXT hex、datetime 存 ISO8601 UTC TEXT、
数组/字典存 JSON 文本、Decimal 成本存 REAL。
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """datetime.utcnow 已在 Python 3.12+ 弃用，统一使用带时区的当前时间"""
    return datetime.now(timezone.utc)


class ContextInfo(BaseModel):
    language: Optional[str] = None
    file_path: Optional[str] = None
    selection: Optional[str] = None
    project_root: Optional[str] = None
    chat_history: Optional[List[Dict[str, str]]] = None


class UserPreferences(BaseModel):
    detail_level: str = "normal"
    code_style: str = "default"
    response_lang: str = "zh"
    # 角色特定偏好扩展命名空间，键值由 config/roles/*.yaml 定义，核心模型不硬编码
    role_specific: Dict[str, Any] = Field(default_factory=dict)
    # 预算降级提示后客户端可携带 force=true 覆盖（§6.2）
    force: bool = False


class RequestMetadata(BaseModel):
    client_type: Optional[str] = None
    client_version: Optional[str] = None
    timestamp: Optional[datetime] = None


class RSIRequest(BaseModel):
    request_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(..., pattern=r"^[a-zA-Z0-9_@.\-]{1,128}$")
    project_id: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    session_id: Optional[str] = None
    # 业务角色标识（developer/test/pm），仅影响意图路由/检索/格式化，无权限语义（§7.2）
    role: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9_\-]{0,49}$")
    raw_input: str = Field(..., min_length=1, max_length=32768)
    intent: Optional[str] = None
    context: ContextInfo = Field(default_factory=ContextInfo)
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)


class RecallRequest(BaseModel):
    """rsi_recall 工具入参（Spec v3.0 §6）：任务描述驱动的记忆召回"""

    task: str = Field(..., min_length=1, max_length=8192)
    project_id: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    user_id: str = Field("local", pattern=r"^[a-zA-Z0-9_@.\-]{1,128}$")
    role: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9_\-]{0,49}$")
    top_k: Optional[int] = Field(None, ge=1, le=10)


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
    # 该内容块适用的业务角色列表，None 表示通用；Postprocessor 按角色过滤（§3.7）
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


class FeedbackAction(str, Enum):
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    IGNORED = "ignored"
    REJECTED = "rejected"
    COPIED = "copied"
    APPLIED = "applied"
    REFERENCED = "referenced"


class Feedback(BaseModel):
    feedback_token: str
    user_id: str = Field(..., pattern=r"^[a-zA-Z0-9_@.\-]{1,128}$")
    project_id: Optional[str] = None
    action: FeedbackAction
    modified_content: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2048)
    timestamp: datetime = Field(default_factory=utcnow)


class KnowledgeItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: str
    title: str
    content: str
    content_type: str = "documentation"
    roles: List[str] = Field(default_factory=list)
    domain: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None
    source_url: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class UserProfile(BaseModel):
    user_id: str
    project_id: Optional[str] = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    frequently_used_intents: Dict[str, int] = Field(default_factory=dict)
    preferred_models: List[str] = Field(default_factory=list)
    feedback_history: List[str] = Field(default_factory=list)
    knowledge_interests: List[str] = Field(default_factory=list)
    # 以下字段由 rsi bootstrap 初始填充（§10.9.4）
    expertise: List[str] = Field(default_factory=list)
    project_insights: Dict[str, Any] = Field(default_factory=dict)
    history_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProcessedRequest(BaseModel):
    """完全解析后的内部中间表达，与原始 RSIRequest 形成清晰区分"""

    request: RSIRequest
    intent: str
    confidence: float = Field(..., ge=0, le=1)
    retrieved_docs: List[KnowledgeItem] = Field(default_factory=list)
    user_profile: Optional[UserProfile] = None
    merged_config: Dict[str, Any] = Field(default_factory=dict)
    additional_context: Dict[str, Any] = Field(default_factory=dict)


def generate_feedback_token(request_id: UUID, user_id: str, secret_key: str) -> str:
    """UUIDv4 + HMAC-SHA256 签名（§2.1）：防伪造，可校验 token 是否属于该请求。

    时序：PreprocessHandler 生成；Log Handler 先 INSERT pending 日志再调模型，
    响应完成后 UPDATE 终态。客户端在 Feedback 中原样提交，服务端校验 HMAC。
    """
    raw = f"{request_id}:{user_id}"
    signature = hmac.new(secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{uuid.uuid4()}-{signature}"


def verify_feedback_token(token: str, request_id: UUID, user_id: str, secret_key: str) -> bool:
    """校验 token 签名部分与 request_id/user_id 是否匹配"""
    if not token or "-" not in token:
        return False
    raw = f"{request_id}:{user_id}"
    expected = hmac.new(secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(token.rsplit("-", 1)[-1], expected)
