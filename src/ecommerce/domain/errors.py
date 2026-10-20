class EcommerceError(Exception):
    """本项目业务异常的基类。"""


class ValidationError(EcommerceError):
    """输入数据不合法。"""
