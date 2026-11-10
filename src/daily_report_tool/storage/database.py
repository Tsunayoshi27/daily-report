"""SQLite 持久化封装（design.md ADR-002）。

约定：
1. 建表 SQL 与 design.md 3.2 **完全一致**，不得增删字段或约束；
2. 所有 SQL 均为参数化查询，禁止字符串拼接用户数据；
3. 连接默认单线程使用（``sqlite3`` 默认 ``check_same_thread=True``），
   与编排流程"先并行采集、后串行入库"的模型一致。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from daily_report_tool.models import DailyReport, ReportSnapshot

_LOGGER = logging.getLogger(__name__)

# 与 design.md 3.2 逐字对齐
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_report (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    author          TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK(role IN ('dev', 'pm', 'ops', 'sales')),
    content         TEXT    NOT NULL,
    is_submitted    INTEGER NOT NULL DEFAULT 0 CHECK(is_submitted IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(date, author)
);

CREATE TABLE IF NOT EXISTS report_snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (report_id) REFERENCES daily_report(id),
    UNIQUE(report_id, version)
);
"""

# SQLite 中 datetime('now', 'localtime') 的返回格式
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_UPSERT_REPORT_SQL = """
INSERT INTO daily_report (date, author, role, content, is_submitted)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(date, author) DO UPDATE SET
    role         = excluded.role,
    content      = excluded.content,
    is_submitted = MAX(daily_report.is_submitted, excluded.is_submitted),
    updated_at   = datetime('now', 'localtime')
"""


def _to_date_text(value: date | datetime | str) -> str:
    """把日期对象统一序列化为 ``YYYY-MM-DD`` 文本。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_datetime(text: str) -> datetime:
    """解析 SQLite 默认时间戳格式。"""
    return datetime.strptime(text, _TIMESTAMP_FORMAT)


class Database:
    """日报记录与版本快照的持久化门面。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    @property
    def path(self) -> Path:
        """数据库文件路径。"""
        return self._path

    def initialize(self) -> None:
        """执行建表脚本（``IF NOT EXISTS``，可重复调用）。"""
        with self._conn:
            self._conn.executescript(SCHEMA_SQL)

    def save_report(self, report: DailyReport) -> int:
        """保存日报并返回主键。

        受 ``UNIQUE(date, author)`` 约束，同一人同一天只会有一条记录：
        若已存在则更新正文、岗位与提交状态，实现"重复运行不产生重复日报"。
        """
        params = (
            _to_date_text(report.date),
            report.author,
            report.role,
            report.content,
            int(bool(report.is_submitted)),
        )
        with self._conn:
            self._conn.execute(_UPSERT_REPORT_SQL, params)
        row = self._conn.execute(
            "SELECT id FROM daily_report WHERE date = ? AND author = ?",
            (params[0], params[1]),
        ).fetchone()
        if row is None:  # pragma: no cover - 插入成功后不可能取不到
            raise RuntimeError("保存日报后未能取回主键")
        return int(row["id"])

    def save_snapshot(self, report_id: int, content: str) -> ReportSnapshot:
        """为指定日报追加一条版本快照，版本号按该日报已有最大值 +1（从 1 起）。

        **正文与最新一版完全相同时不新增版本**（返回既有最新快照）：
        重复运行同一天且内容未变化时不会堆积无意义的相同版本；
        一旦补充了新工作导致正文变化，则正常追加 v2、v3…
        """
        latest = self._conn.execute(
            "SELECT id, report_id, version, content, created_at"
            " FROM report_snapshot WHERE report_id = ? ORDER BY version DESC LIMIT 1",
            (report_id,),
        ).fetchone()
        if latest is not None and latest["content"] == content:
            _LOGGER.debug("日报 %s 正文与最新版本一致，跳过快照", report_id)
            return self._row_to_snapshot(latest)

        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version"
            " FROM report_snapshot WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        version = int(row["next_version"])
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO report_snapshot (report_id, version, content) VALUES (?, ?, ?)",
                (report_id, version, content),
            )
        snapshot_id = int(cursor.lastrowid or 0)
        created = self._conn.execute(
            "SELECT id, report_id, version, content, created_at FROM report_snapshot WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        return self._row_to_snapshot(created)

    def get_report(self, report_id: int) -> DailyReport | None:
        """按主键读取日报，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT id, date, author, role, content, is_submitted, created_at, updated_at"
            " FROM daily_report WHERE id = ?",
            (report_id,),
        ).fetchone()
        return None if row is None else self._row_to_report(row)

    def get_snapshots(self, report_id: int) -> list[ReportSnapshot]:
        """按版本号升序读取某日报的全部快照。"""
        rows = self._conn.execute(
            "SELECT id, report_id, version, content, created_at"
            " FROM report_snapshot WHERE report_id = ? ORDER BY version ASC",
            (report_id,),
        ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def update_report(self, report: DailyReport) -> bool:
        """按主键更新正文、岗位与提交状态，返回是否命中记录。

        Raises:
            ValueError: ``report.id`` 为空（尚未入库的对象无法更新）。
        """
        if report.id is None:
            raise ValueError("update_report 需要已入库的日报对象（id 不能为空）")
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE daily_report"
                " SET role = ?, content = ?, is_submitted = ?,"
                "     updated_at = datetime('now', 'localtime')"
                " WHERE id = ?",
                (
                    report.role,
                    report.content,
                    int(bool(report.is_submitted)),
                    report.id,
                ),
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @staticmethod
    def _row_to_report(row: sqlite3.Row) -> DailyReport:
        # 延迟导入：database 属于 T2、数据模型属于 T3，避免模块级循环依赖
        from daily_report_tool.models import DailyReport

        return DailyReport(
            id=int(row["id"]),
            date=date.fromisoformat(row["date"]),
            author=row["author"],
            role=row["role"],
            content=row["content"],
            is_submitted=bool(row["is_submitted"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> ReportSnapshot:
        # 同上，延迟导入
        from daily_report_tool.models import ReportSnapshot

        return ReportSnapshot(
            id=int(row["id"]),
            report_id=int(row["report_id"]),
            version=int(row["version"]),
            content=row["content"],
            created_at=_parse_datetime(row["created_at"]),
        )
