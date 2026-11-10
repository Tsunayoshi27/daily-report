"""命令行入口（design.md 4.4 ``main``）。

用法::

    uv run daily-report-tool                 # 生成当天日报
    uv run daily-report-tool --date 2026-11-10

流程：加载配置 → 初始化日志（含脱敏）→ Orchestrator 编排 → 返回 DailyReport。
任一环节的异常都会向上抛出（由调用方/CLI 统一记录），符合 design.md 4.4 的契约。
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys

from daily_report_tool.models import DailyReport
from daily_report_tool.orchestrator import Orchestrator
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import DailyReportError
from daily_report_tool.utils.logging_setup import setup_logging

_LOGGER = logging.getLogger(__name__)

_LOG_FILE_NAME = "daily_report.log"


def main(date: datetime.date | None = None) -> DailyReport:
    """生成指定日期的日报。

    Args:
        date: 日报日期，默认当天。

    Returns:
        完整的日报实体（已入库、已尝试推送）。

    Raises:
        ValueError: 配置缺失或非法（如凭证未配置、岗位角色非法）。
        EmptyDataError: 当日没有任何可用工作记录。
        其余各层异常按 design.md 4.4 向上透传。
    """
    config = Config.load()
    setup_logging(config.log_level, config.log_dir / _LOG_FILE_NAME)

    orchestrator = Orchestrator(config)
    try:
        return orchestrator.run(date)
    finally:
        orchestrator.close()


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="daily-report-tool", description="智能日报生成器：采集 → 生成 → 推送"
    )
    parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="日报日期，默认当天",
    )
    return parser


def _cli(argv: list[str] | None = None) -> int:
    """命令行主函数，返回进程退出码。"""
    args = _build_parser().parse_args(argv)
    try:
        report = main(args.date)
    except (DailyReportError, ValueError, OSError) as exc:
        # 日志系统可能尚未初始化成功，这里同时输出到控制台，保证错误可见
        _LOGGER.error("日报生成失败：%s: %s", type(exc).__name__, exc)
        print(f"日报生成失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"日报生成成功：{report.date}（作者 {report.author}，记录 id={report.id}）")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
