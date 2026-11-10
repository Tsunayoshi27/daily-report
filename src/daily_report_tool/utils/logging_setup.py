"""日志初始化与敏感信息脱敏（design.md 6.2 安全要求）。

设计要点：
1. :class:`SensitiveDataFilter` 挂在 **handler** 上（而不是 logger 上），
   这样所有子 logger 经由该 handler 输出的记录都会被过滤；
2. 识别 ``key=value`` / ``key: value`` / ``'key': 'value'`` 以及 ``Bearer xxx``
   两种形态，把凭证值替换为 ``***``；
3. :func:`setup_logging` 幂等：重复调用不会叠加 handler。
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

MASK = "***"

# 需要脱敏的字段名（大小写不敏感）；键名允许带业务前缀，如 GITHUB_TOKEN / SMTP_AUTH_CODE
_SENSITIVE_KEYS = (
    "access_token|refresh_token|tenant_access_token|token"
    "|app_secret|secret"
    "|password|passwd|pwd"
    "|auth_code|authorization"
)

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 形态一：Authorization 头，整段头部值（含 Bearer/Basic 前缀与后续 token）一并脱敏
_AUTH_HEADER_PATTERN = re.compile(r"(?i)\b(authorization)\b(\s*[=:]\s*)([^\r\n]+)")
# 形态二：key=value / key: value / 'key': 'value'
_KEY_VALUE_PATTERN = re.compile(
    rf"(?i)\b((?:[A-Za-z0-9]+[_-])*(?:{_SENSITIVE_KEYS}))\b"
    r"(\s*[=:]\s*)(['\"]?)([^\s'\",;&]+)\3"
)
# 形态三：裸 Bearer token
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._\-]+)")

_HANDLER_MARKER = "_daily_report_handler"


def mask_sensitive(text: str) -> str:
    """把文本中的敏感字段值替换为 ``***``。"""
    masked = _AUTH_HEADER_PATTERN.sub(rf"\1\2{MASK}", text)
    masked = _KEY_VALUE_PATTERN.sub(rf"\1\2\3{MASK}\3", masked)
    return _BEARER_PATTERN.sub(rf"\1\2{MASK}", masked)


class SensitiveDataFilter(logging.Filter):
    """日志脱敏 Filter：命中 token/secret/password/auth_code 的取值一律替换为 ``***``。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """改写日志记录的最终消息，始终返回 True（不丢弃记录）。"""
        message = record.getMessage()
        masked = mask_sensitive(message)
        if masked != message:
            # 消息已就地脱敏，原始格式化参数不再参与输出
            record.msg = masked
            record.args = ()
        return True


def setup_logging(
    level: int | str = logging.INFO,
    log_file: Path | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """初始化根 logger：控制台输出 + 可选滚动文件输出，并统一挂载脱敏 Filter。

    Args:
        level: 日志级别，可传 ``logging.INFO`` 或 ``"INFO"``。
        log_file: 日志文件路径；为 None 时只输出到控制台。
        max_bytes: 单个日志文件最大字节数，超出后滚动。
        backup_count: 保留的历史日志文件数量。
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 复用本模块创建的 handler，保证重复调用不会叠加输出
    console: logging.Handler | None = None
    file_handler: logging.Handler | None = None
    for handler in root.handlers:
        marker = getattr(handler, _HANDLER_MARKER, None)
        if marker == "console":
            console = handler
        elif marker == "file":
            file_handler = handler

    if console is None:
        console = logging.StreamHandler(sys.stdout)
        setattr(console, _HANDLER_MARKER, "console")
        root.addHandler(console)
    if file_handler is None and log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        setattr(file_handler, _HANDLER_MARKER, "file")
        root.addHandler(file_handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    for handler in (console, file_handler):
        if handler is None:
            continue
        handler.setLevel(level)
        handler.setFormatter(formatter)
        # 幂等：先移除旧 Filter 再挂载，避免重复处理
        for existing in [f for f in handler.filters if isinstance(f, SensitiveDataFilter)]:
            handler.removeFilter(existing)
        handler.addFilter(SensitiveDataFilter())
