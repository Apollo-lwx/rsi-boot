"""自定义异常体系。"""


class RSIBootError(Exception):
    """所有 RSI Boot 异常的基类"""


class ConfigError(RSIBootError):
    """配置加载/校验失败"""


class ModelProviderError(RSIBootError):
    """模型调用失败（可重试）。

    countable=False 表示请求自身问题（HTTP 4xx 等），按 §5.1 口径不计入熔断失败计数。
    """

    def __init__(self, message: str, countable: bool = True):
        super().__init__(message)
        self.countable = countable


class ModelTimeoutError(ModelProviderError):
    """模型调用超时"""


class CircuitOpenError(RSIBootError):
    """熔断器 OPEN，请求被快速拒绝（§5.1）"""


class BudgetExceededError(RSIBootError):
    """当日 token 预算超限（§6.2），客户端可带 force=true 覆盖"""


class FeedbackTokenInvalidError(RSIBootError):
    """feedback_token 校验失败（HMAC 不匹配或日志不存在）"""
