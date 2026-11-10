"""SMTP 邮件推送（design.md 4.3 ``push_email``）。

行为约束：
1. 含 3 次重试，间隔 **2s / 4s / 8s**（``max_attempts=4`` = 首次 + 3 次重试）；
2. 凭证（``SMTP_USER`` / ``SMTP_AUTH_CODE``）由 Config 注入，不硬编码、不落日志；
3. 连接/发送失败可重试，**凭证错误不重试**（重试也修不好，直接抛 ``AuthError``）。
"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage

from daily_report_tool.models import DailyReport
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import AuthError, ConnectionError, SendError
from daily_report_tool.utils.retry import retry

_LOGGER = logging.getLogger(__name__)

#: SMTP 连接与收发超时（秒），对应 design.md 6.1「邮件推送耗时 ≤ 5 秒」
_TIMEOUT_SECONDS = 5.0


class EmailPusher:
    """通过 SMTP 把日报发送到指定邮箱。"""

    def __init__(
        self, config: Config, smtp_factory: Callable[[], smtplib.SMTP] | None = None
    ) -> None:
        """初始化推送器。

        Args:
            config: 运行期配置，SMTP 主机/端口/账号/授权码与加密方式均从这里取。
            smtp_factory: 可选的自定义客户端工厂（测试注入替身用）；为 None 时按
                ``smtp_use_ssl`` 自动选择 ``SMTP_SSL`` 或 ``SMTP + STARTTLS``。
        """
        self._config = config
        self._smtp_factory = smtp_factory

    def push_email(self, report: DailyReport, recipients: list[str]) -> bool:
        """发送日报邮件。

        Args:
            report: 待发送的日报实体。
            recipients: 收件人邮箱列表；为空时记录告警并返回 False（不视为错误）。

        Returns:
            发送成功返回 True。

        Raises:
            ConnectionError: SMTP 服务器不可达。
            AuthError: 邮箱账号或授权码无效（不重试）。
            SendError: 投递失败（重试耗尽后抛出）。
        """
        targets = [address.strip() for address in recipients if address and address.strip()]
        if not targets:
            _LOGGER.warning("未配置收件人，跳过邮件推送")
            return False

        message = self._build_message(report, targets)
        self._deliver(message, targets)
        _LOGGER.info("日报邮件已发送：%s -> %s", message["Subject"], targets)
        return True

    @retry(max_attempts=4, base_delay=2.0, exceptions=(ConnectionError, SendError))
    def _deliver(self, message: EmailMessage, recipients: list[str]) -> None:
        """执行一次投递（失败由重试装饰器接管：2s/4s/8s）。"""
        client = self._create_client()
        try:
            client.login(self._config.smtp_user, self._config.smtp_auth_code)
            client.sendmail(self._config.smtp_user, recipients, message.as_string())
        except smtplib.SMTPAuthenticationError as exc:
            # 凭证问题重试无意义，直接上抛且不在重试白名单内
            raise AuthError("SMTP 认证失败，请检查 SMTP_USER / SMTP_AUTH_CODE") from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as exc:
            raise ConnectionError(f"SMTP 连接失败：{exc}") from exc
        except smtplib.SMTPException as exc:
            # 注意：SMTPException 在 Python 3 中继承自 OSError，必须排在 OSError 之前，
            # 否则 SMTPDataError / SMTPRecipientsRefused 等会被误判为"服务器不可达"
            raise SendError(f"邮件发送失败：{exc}") from exc
        except OSError as exc:
            raise ConnectionError(
                f"SMTP 服务器不可达：{self._config.smtp_host}:{self._config.smtp_port}"
                f"（{type(exc).__name__}）"
            ) from exc
        finally:
            self._close(client)

    def _create_client(self) -> smtplib.SMTP:
        """按配置创建 SMTP 客户端（465 端口用 SSL，587 端口用 STARTTLS）。"""
        if self._smtp_factory is not None:
            return self._smtp_factory()
        if self._config.smtp_use_ssl:
            return smtplib.SMTP_SSL(
                self._config.smtp_host, self._config.smtp_port, timeout=_TIMEOUT_SECONDS
            )
        client = smtplib.SMTP(
            self._config.smtp_host, self._config.smtp_port, timeout=_TIMEOUT_SECONDS
        )
        client.starttls()
        return client

    def _build_message(self, report: DailyReport, recipients: list[str]) -> EmailMessage:
        """构造纯文本邮件（主题格式与导出文件名保持一致）。"""
        message = EmailMessage()
        message["Subject"] = f"日报_{report.date.isoformat()}_{report.author}"
        message["From"] = f"{report.author} <{self._config.smtp_user}>"
        message["To"] = ", ".join(recipients)
        message.set_content(report.content, subtype="plain", charset="utf-8")
        return message

    @staticmethod
    def _close(client: smtplib.SMTP) -> None:
        """尽力退出连接，避免占用服务端会话。"""
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - 关闭阶段异常不应覆盖原始错误
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
