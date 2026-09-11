"""提示词渲染（§3.4）：Jinja2 模板 + 变量注入 + 上下文截断。

截断规则：chat_history 最多保留最近 10 轮；渲染后超模型窗口时保留 system + 最近 2 轮。
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any, List, Optional

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from ..core.models import KnowledgeItem, ProcessedRequest, UserProfile

logger = logging.getLogger(__name__)

_MAX_HISTORY_TURNS = 10
_DEGRADED_HISTORY_TURNS = 2
# 粗略 token 估算：4 字符 ≈ 1 token（M1 足够，精确 tiktoken 计数在 P4.4 优化）
_CHARS_PER_TOKEN = 4


def _template_dirs() -> List[str]:
    # 用户覆写层（~/.rsi/templates，§3.9 prompt_template 槽提案落点）优先；
    # 角色模板（config/role_templates/）与通用模板（config/templates/）同空间解析
    import os
    from pathlib import Path

    dirs = [str(Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi"))) / "templates")]
    base = resources.files("rsi_boot") / "config"
    dirs += [str(base / "templates"), str(base / "role_templates")]
    return dirs


class PromptRenderer:
    def __init__(self) -> None:
        # importlib.resources Traversable 在源码布局下即真实路径
        self._env = Environment(
            loader=ChoiceLoader([FileSystemLoader(d) for d in _template_dirs()]),
            autoescape=select_autoescape(disabled_extensions=("jinja2",)),
            keep_trailing_newline=True,
        )

    def render(
        self,
        template_ref: str,
        processed: ProcessedRequest,
        max_prompt_tokens: int = 6000,
    ) -> List[dict[str, str]]:
        """渲染为 OpenAI messages 结构（system + 历史 + user）"""
        request = processed.request
        profile: Optional[UserProfile] = processed.user_profile

        history = (request.context.chat_history or [])[-_MAX_HISTORY_TURNS:]
        variables: dict[str, Any] = {
            "project_name": request.project_id or "",
            "project_context": (profile.project_insights.get("summary") if profile else "") or "",
            "user_expertise": profile.expertise if profile else [],
            "user_style": request.preferences.code_style,
            "knowledge_items": processed.retrieved_docs,
            "code_content": request.context.selection or "",
            "language": request.context.language or "",
        }

        try:
            template = self._env.get_template(template_ref)
        except TemplateNotFound:
            logger.warning("模板 %s 不存在，回退 general_assist.jinja2", template_ref)
            template = self._env.get_template("general_assist.jinja2")

        system_prompt = template.render(**variables)
        messages: List[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": m.get("role", "user"), "content": m.get("content", "")} for m in history)
        messages.append({"role": "user", "content": request.raw_input})

        # 超窗口降级：保留 system + 最近 2 轮历史 + 当前输入
        total_chars = sum(len(m["content"]) for m in messages)
        if total_chars / _CHARS_PER_TOKEN > max_prompt_tokens and len(messages) > 3:
            messages = (
                messages[:1]
                + messages[-(_DEGRADED_HISTORY_TURNS + 1):]
            )
        return messages
