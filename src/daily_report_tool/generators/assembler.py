"""日报素材组装器（design.md 4.2 ``assemble_report``）。

把采集层零散的 RawRecord 按 ``status`` 归入三段：
``done`` → 今日完成，``pending`` → 明日计划，``blocked`` → 风险阻塞。
"""

from __future__ import annotations

import logging

from daily_report_tool.models import RawRecord, Role
from daily_report_tool.utils.exceptions import EmptyDataError

_LOGGER = logging.getLogger(__name__)

#: status 枚举值 → 分组键（与 design.md 4.2 的出参结构一致）
_STATUS_TO_GROUP: dict[str, str] = {"done": "done", "pending": "pending", "blocked": "blocked"}


class ReportAssembler:
    """按 ``status`` 字段分组，产出生成层可直接消费的三段素材。"""

    def assemble_report(
        self, records: list[RawRecord], author: str, role: str
    ) -> dict[str, list[RawRecord]]:
        """把原始记录分组为三段素材。

        Args:
            records: 采集层汇总的原始记录。
            author: 日报作者（用于日志与上下文校验）。
            role: 岗位角色，必须是 ``dev`` / ``pm`` / ``ops`` / ``sales``。

        Returns:
            ``{"done": [...], "pending": [...], "blocked": [...]}``，各组按时间升序排列。

        Raises:
            EmptyDataError: ``records`` 为空（禁止凭空编造日报）。
            ValueError: 岗位角色非法。
        """
        if not records:
            raise EmptyDataError("未采集到任何原始记录，无法组装日报（禁止编造内容）")

        try:
            role_value = Role(role).value
        except ValueError as exc:
            allowed = " / ".join(member.value for member in Role)
            raise ValueError(f"不支持的岗位角色：{role!r}，仅支持 {allowed}") from exc

        grouped: dict[str, list[RawRecord]] = {"done": [], "pending": [], "blocked": []}
        for record in records:
            grouped[_STATUS_TO_GROUP[record.status.value]].append(record)
        for items in grouped.values():
            items.sort(key=lambda item: item.timestamp)

        _LOGGER.info(
            "日报素材组装完成：作者=%s 岗位=%s 完成=%d 计划=%d 阻塞=%d",
            author,
            role_value,
            len(grouped["done"]),
            len(grouped["pending"]),
            len(grouped["blocked"]),
        )
        return grouped
