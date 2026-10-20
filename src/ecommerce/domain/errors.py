class EcommerceError(Exception):
    """本项目业务异常的基类。"""


class AuthError(EcommerceError):
    """401/403：刷新 token 后仍失败，停止任务。"""


class RateLimitError(EcommerceError):
    """429 / E901：可重试，但要尊重 Retry-After。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(EcommerceError):
    """5xx、超时、连接错误：可重试，但要有上限。"""


class ValidationError(EcommerceError):
    """输入不合法，或 4xx 参数/业务校验失败。"""


class PermanentError(EcommerceError):
    """其他 4xx：不重试，进死信。"""


class ParseError(EcommerceError):
    """2xx 但 JSON/XML 非法：有限重试，仍失败进死信。"""
