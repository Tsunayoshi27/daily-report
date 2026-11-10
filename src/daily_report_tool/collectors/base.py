"""采集层抽象基类（design.md 4.1）。

所有数据源统一实现 ``collect(day) -> list[RawRecord]``：
新增数据源（企业微信 / Jira …）只需新增一个子类，不动编排层（design.md 6.5 可维护性要求）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import ClassVar

from daily_report_tool.models import RawRecord, Source


def day_window(day: date) -> tuple[str, str]:
    """返回本地时区下当天 ``[00:00, 次日 00:00)`` 的 ISO8601 时间串（用于 since/until 查询）。"""
    start = datetime.combine(day, dt_time.min).astimezone()
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def parse_iso_timestamp(text: object) -> datetime | None:
    """把 ISO8601 时间串（含 ``Z`` 后缀）解析为本地 naive 时间；无法解析时返回 None。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


class Collector(ABC):
    """数据源采集器统一接口。"""

    source: ClassVar[Source]

    @abstractmethod
    def collect(self, day: date) -> list[RawRecord]:
        """采集指定日期的原始工作记录。

        Args:
            day: 采集日期。

        Returns:
            该日期的原始记录列表；数据源当日无数据时返回空列表。

        Raises:
            各子类按 design.md 4.1 声明：``ConnectionError`` / ``AuthError`` /
            ``RateLimitError`` / ``TokenExpiredError``。
        """

    @property
    def name(self) -> str:
        """数据源标识，用于日志与编排层展示。"""
        return self.source.value
