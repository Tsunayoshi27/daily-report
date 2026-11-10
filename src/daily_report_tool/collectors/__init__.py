"""采集层：从各数据源拉取当日工作记录，统一归一为 RawRecord。"""

from daily_report_tool.collectors.base import Collector, day_window, parse_iso_timestamp
from daily_report_tool.collectors.feishu import FeishuCollector
from daily_report_tool.collectors.gitee import GiteeCollector
from daily_report_tool.collectors.github import GitHubCollector
from daily_report_tool.collectors.mock import MockCollector

__all__ = [
    "Collector",
    "FeishuCollector",
    "GiteeCollector",
    "GitHubCollector",
    "MockCollector",
    "day_window",
    "parse_iso_timestamp",
]
