"""模拟数据采集器（design.md 4.1 ``collect_mock``）。

用途：
1. 开发调试与凭证未就绪时验证主流程；
2. 集成测试（T8）中作为无外部依赖的数据源。

返回 5 条固定记录（覆盖 done / pending / blocked 三种状态），保证三段式日报的每一段都有内容可见。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import ClassVar

from daily_report_tool.collectors.base import Collector
from daily_report_tool.models import RawRecord, RecordStatus, Source

MOCK_AUTHOR = "模拟用户"


@dataclass(frozen=True, slots=True)
class _MockTemplate:
    """模拟记录模板，``at`` 为当天的时间点。"""

    at: time
    project: str
    title: str
    detail: str
    status: RecordStatus
    url: str = ""


_TEMPLATES: tuple[_MockTemplate, ...] = (
    _MockTemplate(
        at=time(9, 30),
        project="智能日报生成器",
        title="完成采集层接口设计",
        detail="定义 Collector.collect(day) 统一契约，并确定 GitHub/飞书字段映射规则",
        status=RecordStatus.DONE,
        url="https://github.com/Tsunayoshi27/daily-report/pull/1",
    ),
    _MockTemplate(
        at=time(11, 15),
        project="智能日报生成器",
        title="修复日报去重逻辑缺陷",
        detail="同一人同一天重复生成时复用既有记录，避免产生重复日报",
        status=RecordStatus.DONE,
        url="https://github.com/Tsunayoshi27/daily-report/issues/12",
    ),
    _MockTemplate(
        at=time(14, 40),
        project="智能日报生成器",
        title="补充日志脱敏单元验证",
        detail="覆盖 GITHUB_TOKEN / app_secret / 邮箱授权码三种形态的脱敏断言",
        status=RecordStatus.DONE,
    ),
    _MockTemplate(
        at=time(16, 10),
        project="智能日报生成器",
        title="联调邮件推送超时重试",
        detail="验证 SMTP 在 2s/4s/8s 退避后成功送达",
        status=RecordStatus.PENDING,
    ),
    _MockTemplate(
        at=time(17, 50),
        project="智能日报生成器",
        title="等待飞书应用任务读取权限审批",
        detail="当前应用仅有通讯录权限，任务接口调用返回 99991672，需管理员补充授权",
        status=RecordStatus.BLOCKED,
    ),
)


class MockCollector(Collector):
    """本地模拟数据采集器，不依赖任何外部服务。"""

    source: ClassVar[Source] = Source.MOCK

    def collect(self, day: date) -> list[RawRecord]:
        """实现统一接口，等价于 :meth:`collect_mock`。"""
        return self.collect_mock(day)

    def collect_mock(self, day: date) -> list[RawRecord]:
        """返回指定日期的 5 条模拟记录（``source="mock"``）。

        Raises:
            无。本采集器不访问外部服务。
        """
        return [
            RawRecord(
                source=Source.MOCK,
                timestamp=datetime.combine(day, template.at),
                project=template.project,
                title=template.title,
                detail=template.detail,
                status=template.status,
                url=template.url,
                author=MOCK_AUTHOR,
            )
            for template in _TEMPLATES
        ]
