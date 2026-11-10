"""统一异常定义。

design.md 第 4 节各接口「错误码」列出的异常，全部在此集中定义。
所有业务异常均派生自 :class:`DailyReportError`，便于主编排入口按层统一捕获并降级：

- 采集层：``ConnectionError`` / ``AuthError`` / ``RateLimitError`` / ``TokenExpiredError``
- 生成层：``LLMTimeoutError`` / ``LLMConnectionError`` / ``EmptyResponseError``
  / ``EmptyDataError`` / ``TemplateError``
- 推送层：``ConnectionError`` / ``AuthError`` / ``SendError``

说明：``ConnectionError`` 与内置异常同名，这是 design.md 的既定契约，故不改名；
在需要用到内置 ``ConnectionError`` 的模块中请显式写 ``builtins.ConnectionError``。
"""

from __future__ import annotations


class DailyReportError(Exception):
    """本项目所有业务异常的基类。"""


class ConnectionError(DailyReportError):
    """外部服务不可达（GitHub / 飞书 / SMTP）。"""


class AuthError(DailyReportError):
    """凭证无效或过期。"""


class RateLimitError(DailyReportError):
    """外部 API 触发速率限制。"""


class TokenExpiredError(DailyReportError):
    """飞书 tenant_access_token 已过期，需要重新获取。"""


class LLMTimeoutError(DailyReportError):
    """LLM 调用超过 30 秒超时阈值。"""


class LLMConnectionError(DailyReportError):
    """LLM 服务不可达。"""


class EmptyResponseError(DailyReportError):
    """LLM 返回空内容。"""


class EmptyDataError(DailyReportError):
    """采集结果为空，无法组装日报。"""


class TemplateError(DailyReportError):
    """模板缺失或格式错误。"""


class SendError(DailyReportError):
    """邮件发送失败。"""
