"""核心数据模型（design.md 3.1）。

三个实体与 design.md 3.1 的字段表一一对应，枚举值精确匹配：

- ``RawRecord.source``：``github`` / ``feishu`` / ``mock`` / ``manual``
- ``RawRecord.status``：``done`` / ``pending`` / ``blocked``
- ``DailyReport.role``：``dev`` / ``pm`` / ``ops`` / ``sales``

字段顺序与 design.md 3.1 的字段表**逐行一致**（必填项夹在可选项之间），
因此三个实体均声明为 ``kw_only``，实例化时必须使用关键字参数。
构造时立即校验，非法值抛 ``ValueError``，避免脏数据流到生成层或数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

# design.md 3.1：RawRecord.title 长度 ≤ 500
TITLE_MAX_LENGTH = 500


class Source(StrEnum):
    """数据来源。"""

    GITHUB = "github"
    GITEE = "gitee"
    FEISHU = "feishu"
    MOCK = "mock"
    MANUAL = "manual"


class RecordStatus(StrEnum):
    """原始记录完成状态，决定日报三段归类（done→今日完成，pending→明日计划，blocked→风险阻塞）。"""

    DONE = "done"
    PENDING = "pending"
    BLOCKED = "blocked"


class Role(StrEnum):
    """岗位角色，决定话术库匹配。"""

    DEV = "dev"
    PM = "pm"
    OPS = "ops"
    SALES = "sales"


def _coerce[E: StrEnum](enum_type: type[E], value: str | E, field_name: str) -> E:
    """把字符串归一化为枚举成员，非法值抛 ``ValueError``。"""
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = " / ".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} 取值非法：{value!r}，仅支持 {allowed}") from exc


@dataclass(slots=True, kw_only=True)
class RawRecord:
    """采集层输出的标准化原始记录（内存对象，不入库）。"""

    source: Source | str
    timestamp: datetime
    project: str
    title: str
    detail: str = ""
    status: RecordStatus | str
    url: str = ""
    author: str

    def __post_init__(self) -> None:
        self.source = _coerce(Source, self.source, "source")
        self.status = _coerce(RecordStatus, self.status, "status")
        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp 必须是 datetime 实例")
        for name in ("project", "title", "author"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} 不能为空")
        if len(self.title) > TITLE_MAX_LENGTH:
            raise ValueError(
                f"title 长度不得超过 {TITLE_MAX_LENGTH} 字符，当前 {len(self.title)} 字符"
            )


@dataclass(slots=True, kw_only=True)
class DailyReport:
    """日报实体，对应 ``daily_report`` 表一行。"""

    # id 由数据库自增赋值，新建对象时留空
    id: int | None = None
    date: date
    author: str
    role: Role | str
    content: str
    is_submitted: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.role = _coerce(Role, self.role, "role")
        if not isinstance(self.date, date):
            raise ValueError("date 必须是 date 实例")
        if not str(self.author).strip():
            raise ValueError("author 不能为空")
        if not str(self.content).strip():
            raise ValueError("content 不能为空")
        self.is_submitted = bool(self.is_submitted)


@dataclass(slots=True, kw_only=True)
class ReportSnapshot:
    """日报版本快照，对应 ``report_snapshot`` 表一行。"""

    id: int | None = None
    report_id: int
    version: int
    content: str
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if self.report_id is None:
            raise ValueError("report_id 不能为空")
        if self.version < 1:
            raise ValueError("version 从 1 开始递增")
        if not str(self.content).strip():
            raise ValueError("content 不能为空")
