"""主编排入口（design.md 4.4，tasks T7）。

编排顺序固定为：

    采集（3 数据源并行）→ 组装 → LLM 生成 → 空话过滤 → 模板渲染 → 入库 → 推送

设计原则：
1. **失败不阻断**：单个采集源失败只丢该源；LLM 失败由生成层降级；模板/入库/推送失败
   记录日志后继续，尽可能把日报交到员工手上；
2. **不编造**：真实数据源无记录时才启用 mock 数据（并显著告警），一旦有真实记录就丢弃 mock；
3. **全链路日志**：每一步都记录耗时与结果，满足 design.md 6.5「日志完整」。
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from daily_report_tool.collectors.base import Collector
from daily_report_tool.collectors.feishu import FeishuCollector
from daily_report_tool.collectors.gitee import GiteeCollector
from daily_report_tool.collectors.github import GitHubCollector
from daily_report_tool.collectors.mock import MockCollector
from daily_report_tool.generators.assembler import ReportAssembler
from daily_report_tool.generators.filter import ContentFilter
from daily_report_tool.generators.llm import LLMGenerator
from daily_report_tool.generators.template import TemplateEngine
from daily_report_tool.models import DailyReport, RawRecord, Source
from daily_report_tool.pushers.email import EmailPusher
from daily_report_tool.pushers.file import FileExporter
from daily_report_tool.storage.database import Database
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import (
    AuthError,
    ConnectionError,
    EmptyDataError,
    SendError,
    TemplateError,
)

_LOGGER = logging.getLogger(__name__)

#: 采集层最大并发线程数（design.md 6.1：3 个数据源并行）
MAX_COLLECT_WORKERS = 3

#: 导出格式（proposal 3.1：支持 Markdown、Word）
EXPORT_FORMATS: tuple[str, ...] = ("md", "docx")


class Orchestrator:
    """按固定顺序调度各级组件，产出完整日报。"""

    def __init__(
        self,
        config: Config,
        collectors: Sequence[Collector] | None = None,
        db: Database | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            config: 运行期配置（作者、岗位、仓库、推送与存储路径均来自这里）。
            collectors: 可选的自定义采集器列表（测试注入用）；默认
                GitHub + 飞书 + Mock 三个数据源。
            db: 可选的数据库实例（测试注入用）；默认按 ``config.db_path`` 新建。
        """
        self._config = config
        self._collectors: list[Collector] = (
            list(collectors) if collectors is not None else self._default_collectors()
        )
        self._owns_db = db is None
        self._db = db if db is not None else Database(config.db_path)

        self._assembler = ReportAssembler()
        self._llm = LLMGenerator(config)
        self._content_filter = ContentFilter()
        self._template = TemplateEngine()
        self._exporter = FileExporter(config)
        self._email = EmailPusher(config)

    def run(self, date: datetime.date | None = None) -> DailyReport:
        """执行完整日报流程。

        Args:
            date: 日报日期，默认当天。

        Returns:
            已入库（若入库成功）并已尝试推送的日报实体。

        Raises:
            EmptyDataError: 所有数据源都没有可用记录——此时拒绝凭空生成日报。
            ValueError: 配置中的岗位角色非法。
        """
        day = date or datetime.date.today()
        started = time.monotonic()
        _LOGGER.info(
            "=== 日报生成开始：date=%s author=%s role=%s ===",
            day,
            self._config.report_author,
            self._config.report_role,
        )

        records = self._collect(day)
        grouped = self._assemble(records, day)
        body = self._generate(grouped)
        filtered, warnings = self._filter(body)
        report = self._render(day, filtered)
        report = self._persist(report)
        self._push(report)

        _LOGGER.info(
            "=== 日报生成完成：date=%s id=%s 耗时 %.2fs ===",
            day,
            report.id,
            time.monotonic() - started,
        )
        return report

    def close(self) -> None:
        """释放数据库连接与采集器的 HTTP 客户端。"""
        for collector in self._collectors:
            closer = getattr(collector, "close", None)
            if callable(closer):
                closer()
        if self._owns_db:
            self._db.close()

    def __enter__(self) -> Orchestrator:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ---------------- 各步骤 ----------------

    def _default_collectors(self) -> list[Collector]:
        """默认数据源：Gitee、GitHub、飞书、Mock（未配置的源会立即返回空列表，不占用等待时间）。

        Gitee 排在首位：团队主战场在 Gitee，跨源去重时优先保留 Gitee 的归属信息。
        """
        return [
            GiteeCollector(self._config),
            GitHubCollector(self._config),
            FeishuCollector(self._config),
            MockCollector(),
        ]

    def _collect(self, day: datetime.date) -> list[RawRecord]:
        """并行采集三数据源，单源失败不影响其他源。"""
        started = time.monotonic()
        results: dict[str, list[RawRecord]] = {}
        workers = max(1, min(MAX_COLLECT_WORKERS, len(self._collectors)))

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collector") as pool:
            futures = {
                pool.submit(self._collect_one, collector, day): collector
                for collector in self._collectors
            }
            for future in as_completed(futures):
                collector = futures[future]
                try:
                    results[collector.name] = future.result()
                except Exception as exc:  # noqa: BLE001 - 单源失败必须被隔离，继续用其他源
                    _LOGGER.error(
                        "采集源 %s 失败，已跳过：%s: %s", collector.name, type(exc).__name__, exc
                    )
                    results[collector.name] = []

        for name, items in sorted(results.items()):
            _LOGGER.info("采集源 %s 返回 %d 条记录", name, len(items))

        # 按采集器声明顺序重排，保证跨源去重时归属稳定（不受网络返回先后影响）
        ordered = {
            collector.name: results.get(collector.name, []) for collector in self._collectors
        }
        records = self._merge_sources(ordered)
        _LOGGER.info("采集完成：共 %d 条记录（%.2fs）", len(records), time.monotonic() - started)
        return records

    @staticmethod
    def _collect_one(collector: Collector, day: datetime.date) -> list[RawRecord]:
        """线程池任务体：调用单个采集器。"""
        _LOGGER.info("开始采集：%s", collector.name)
        return collector.collect(day)

    @staticmethod
    def _merge_sources(results: dict[str, list[RawRecord]]) -> list[RawRecord]:
        """合并各源记录：有真实数据时丢弃 mock，只有 mock 时才使用 mock。"""
        mock_key = Source.MOCK.value
        real = [record for name, items in results.items() if name != mock_key for record in items]
        mock = results.get(mock_key, [])

        if real:
            if mock:
                _LOGGER.info("已获取 %d 条真实记录，丢弃 %d 条 mock 数据", len(real), len(mock))
            return Orchestrator._dedupe(real)
        if mock:
            _LOGGER.warning(
                "真实数据源未返回任何记录，改用 %d 条 mock 数据维持主流程（请检查凭证与权限）",
                len(mock),
            )
            return Orchestrator._dedupe(mock)
        return []

    @staticmethod
    def _dedupe(records: list[RawRecord]) -> list[RawRecord]:
        """跨源去重：同一工作项被多个托管平台镜像时只保留一条。

        判定依据为「标题 + 时间戳」——镜像仓库的提交信息与作者时间完全一致，
        因此能准确识别重复；不同项目同名但时间不同的记录不会被误删。
        """
        seen: set[tuple[str, datetime.datetime]] = set()
        unique: list[RawRecord] = []
        for record in records:
            key = (record.title.strip(), record.timestamp)
            if key in seen:
                continue
            seen.add(key)
            unique.append(record)
        dropped = len(records) - len(unique)
        if dropped:
            _LOGGER.info("跨源去重：丢弃 %d 条重复记录（同一提交在多平台镜像）", dropped)
        return unique

    def _assemble(self, records: list[RawRecord], day: datetime.date) -> dict[str, list[RawRecord]]:
        """按状态分组。

        Raises:
            EmptyDataError: 当日无任何可用记录。
        """
        started = time.monotonic()
        try:
            grouped = self._assembler.assemble_report(
                records, self._config.report_author, self._config.report_role
            )
        except EmptyDataError:
            _LOGGER.error("当日（%s）无任何工作记录，终止生成（禁止编造日报）", day)
            raise
        _LOGGER.info("组装完成（%.2fs）", time.monotonic() - started)
        return grouped

    def _generate(self, grouped: dict[str, list[RawRecord]]) -> str:
        """调用 LLM 生成正文；生成层内部已做降级，不会中断流程。"""
        started = time.monotonic()
        body = self._llm.generate_report(
            grouped, self._config.report_author, self._config.report_role
        )
        if self._llm.degraded_reason:
            _LOGGER.warning("LLM 已降级为纯模板方案：%s", self._llm.degraded_reason)
        _LOGGER.info(
            "生成完成：provider=%s 字符数=%d（%.2fs）",
            self._llm.provider,
            len(body),
            time.monotonic() - started,
        )
        return body

    def _filter(self, body: str) -> tuple[str, list[str]]:
        """过滤空话套话，并记录警告。"""
        started = time.monotonic()
        filtered, warnings = self._content_filter.filter_content(body)
        for warning in warnings:
            _LOGGER.warning("内容过滤：%s", warning)
        _LOGGER.info("过滤完成：警告 %d 条（%.2fs）", len(warnings), time.monotonic() - started)
        return filtered, warnings

    def _render(self, day: datetime.date, body: str) -> DailyReport:
        """套用三段式模板；渲染失败时退回未格式化正文，保证仍有产出。"""
        started = time.monotonic()
        report = DailyReport(
            date=day,
            author=self._config.report_author,
            role=self._config.report_role,
            content=body,
        )
        try:
            report.content = self._template.render_template(report, self._config.report_role)
        except TemplateError as exc:
            _LOGGER.error("模板渲染失败，降级为原始正文：%s", exc)
        _LOGGER.info(
            "模板渲染完成：%d 字符（%.2fs）", len(report.content), time.monotonic() - started
        )
        return report

    def _persist(self, report: DailyReport) -> DailyReport:
        """入库并保存 AI 初稿快照；失败只记录日志，不阻断推送。"""
        started = time.monotonic()
        try:
            report.id = self._db.save_report(report)
            self._db.save_snapshot(report.id, report.content)
        except (sqlite3.Error, OSError) as exc:
            _LOGGER.error("日报入库失败（继续推送）：%s: %s", type(exc).__name__, exc)
            return report
        _LOGGER.info("入库完成：id=%s（%.2fs）", report.id, time.monotonic() - started)
        return report

    def _push(self, report: DailyReport) -> None:
        """导出文件 + 邮件推送；各渠道独立失败互不影响。"""
        for export_format in EXPORT_FORMATS:
            try:
                path = self._exporter.export_file(report, export_format)
            except (ValueError, OSError) as exc:
                _LOGGER.error("导出 %s 失败：%s", export_format, exc)
                continue
            _LOGGER.info("导出成功：%s", path)

        recipients = list(self._config.email_recipients)
        if not recipients:
            _LOGGER.info("未配置 EMAIL_RECIPIENTS，跳过邮件推送")
            return
        try:
            if self._email.push_email(report, recipients):
                _LOGGER.info("邮件推送成功：%d 个收件人", len(recipients))
        except (AuthError, ConnectionError, SendError) as exc:
            _LOGGER.error("邮件推送失败：%s: %s", type(exc).__name__, exc)
