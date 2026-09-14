"""rsi_feedback 工具（§4.2，P2.5）：显式反馈 + 隐式行为上报协议。

校验链：token 存在 → HMAC 签名匹配（request_id + user_id）→ 同步落库日志字段
（显式反馈在响应返回前落库）→ 画像/策略更新投递 asyncio.Queue 异步处理。
隐式动作（accepted/applied/modified/copied/referenced/ignored）由 IDE 集成层捕获上报。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...core.models import verify_feedback_token
from ...feedback.implicit_tracker import ALL_ACTIONS, FeedbackWorker, ImplicitEvent
from ...services.log_service import LogService
from ...ux.lang import locale_lang
from ...ux.messages import TOOL_DESC, t

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_feedback"
TOOL_DESCRIPTION = TOOL_DESC["feedback"]

_ACTIONS = sorted(ALL_ACTIONS)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "feedback_token": {"type": "string", "description": "rsi_recall 返回的反馈令牌"},
        "action": {"type": "string", "enum": _ACTIONS,
                   "description": "accepted/applied=采纳应用 modified=修改后采纳 copied=复制 "
                                  "referenced=参考 ignored=忽略 rejected=拒绝"},
        "rating": {"type": "integer", "minimum": 1, "maximum": 5,
                   "description": "可选 1-5 评分（与 action 可叠加，分别生效）"},
        "modified_content": {"type": "string",
                             "description": "action=modified 时的修改后内容（用于 diff 比例计算）"},
        "comment": {"type": "string", "maxLength": 2048,
                    "description": "可选评语；action=rejected 时填写将提炼为禁止项（§4.4）"},
    },
    "required": ["feedback_token", "action"],
}


async def handle(
    logs: LogService,
    secret: str,
    arguments: dict[str, Any],
    worker: Optional[FeedbackWorker] = None,
) -> dict[str, Any]:
    token = str(arguments.get("feedback_token", ""))
    action = str(arguments.get("action", ""))
    rating = arguments.get("rating")
    modified_content = arguments.get("modified_content")
    comment = arguments.get("comment")

    lang = locale_lang()
    if action not in ALL_ACTIONS:
        return {"status": "error", "message": t("FEEDBACK_ACTION_INVALID", lang, actions=_ACTIONS)}
    if rating is not None and not (1 <= int(rating) <= 5):
        return {"status": "error", "message": t("FEEDBACK_RATING_RANGE", lang)}

    context = await logs.get_token_context(token)
    if context is None:
        return {"status": "error", "message": "feedback_token 不存在"}
    request_id, user_id = context
    if not verify_feedback_token(token, request_id, user_id, secret):
        return {"status": "error", "message": "feedback_token 签名校验失败"}

    # 显式反馈同步落库（§4.2：响应返回前完成日志更新）
    await logs.apply_feedback(
        token, action, int(rating) if rating is not None else None,
        comment=str(comment) if comment else None,
    )

    # 策略/画像更新走异步队列，不阻塞响应
    queued = False
    if worker is not None:
        queued = worker.submit(ImplicitEvent(
            feedback_token=token, action=action,
            rating=int(rating) if rating is not None else None,
            modified_content=modified_content,
            comment=str(comment) if comment else None,
        ))
    return {"status": "ok", "action": action, "queued": queued}
