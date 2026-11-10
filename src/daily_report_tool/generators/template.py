"""三段式模板引擎（design.md 4.2 ``render_template``，ADR-003）。

本模块是「三段式结构」的**唯一权威定义**：段标题、段落顺序、岗位话术库都在这里，
生成层的其它模块（LLMGenerator / ContentFilter）通过导入常量复用同一套结构，
保证「格式合规率 = 100%」。

模板结构不可变（员工只能改正文文字），岗位差异只体现在措辞上：
标题、岗位称谓、以及每段标题后的岗位化括注（如「今日完成（研发交付）」）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from daily_report_tool.models import DailyReport, Role
from daily_report_tool.utils.exceptions import TemplateError

_LOGGER = logging.getLogger(__name__)

SECTION_DONE = "今日完成"
SECTION_PLAN = "明日计划"
SECTION_RISK = "风险阻塞"
SECTION_ORDER: tuple[str, ...] = (SECTION_DONE, SECTION_PLAN, SECTION_RISK)

# 段标题的容错识别：兼容「今日完成」「【今日完成】」「## 今日完成」「今日完成：」等写法
SECTION_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[\[【]\s*)?"
    r"(?P<name>今日完成|明日计划|风险阻塞)"
    r"(?:\s*[\]】])?\s*[:：]?\s*$"
)

_BULLET_PREFIXES: tuple[str, ...] = ("-", "*", "•", "·")


def _is_preformatted(item: str) -> bool:
    """已是列表项或以 ``**`` 包裹的子标题，原样输出。"""
    return item.startswith(_BULLET_PREFIXES) or item.startswith("**")


@dataclass(frozen=True, slots=True)
class RoleWording:
    """岗位话术库条目，对应 proposal「研发/产品/运营/销售 4 套专属话术库」。"""

    label: str
    """岗位称谓，用于标题与岗位行。"""

    qualifiers: tuple[str, str, str]
    """三段标题后的岗位化括注（今日完成 / 明日计划 / 风险阻塞）。"""

    empty_placeholder: str = "无"
    """该段无记录时的占位语，避免编造内容。"""


_ROLE_WORDING: dict[Role, RoleWording] = {
    Role.DEV: RoleWording("研发工程师", ("研发交付", "研发排期", "研发风险")),
    Role.PM: RoleWording("产品经理", ("需求推进", "需求排期", "产品风险")),
    Role.OPS: RoleWording("运维工程师", ("运维保障", "运维计划", "运维风险")),
    Role.SALES: RoleWording("销售代表", ("客户跟进", "客户计划", "销售风险")),
}


class TemplateEngine:
    """把日报正文渲染为固定三段式文本。"""

    def render_template(self, report: DailyReport, role: str) -> str:
        """套用三段式模板并匹配岗位话术库。

        Args:
            report: 日报实体，使用 ``report.content`` 作为三段正文来源。
            role: 岗位角色，用于匹配话术库（``dev`` / ``pm`` / ``ops`` / ``sales``）。

        Returns:
            带标题头、作者行、岗位行、日期行与三段结构的完整文本。

        Raises:
            TemplateError: 正文缺少三段结构标记，或岗位角色不受支持。
        """
        wording = self._resolve_wording(role)
        preamble, sections = self._split_sections(report.content)

        lines: list[str] = [
            f"# {wording.label}工作日报",
            "",
            f"- 作者：{report.author}",
            f"- 岗位：{wording.label}（{self._role_value(role)}）",
            f"- 日期：{report.date.isoformat()}",
        ]
        for item in preamble:
            # 正文首部的工作量统计提升到抬头区，便于一眼看到"我做了什么"
            lines.append(f"- {item}" if not item.startswith("-") else item)
        lines.append("")

        for index, name in enumerate(SECTION_ORDER):
            lines.append(f"## {name}（{wording.qualifiers[index]}）")
            body = sections[name] or [f"- {wording.empty_placeholder}"]
            for item in body:
                lines.append(item if _is_preformatted(item) else f"- {item}")
            lines.append("")

        _LOGGER.debug("模板渲染完成：作者=%s 岗位=%s", report.author, role)
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _role_value(role: str) -> str:
        """返回角色枚举值（用于展示），非法角色由 :meth:`_resolve_wording` 提前拦截。"""
        return Role(role).value

    @staticmethod
    def _resolve_wording(role: str) -> RoleWording:
        """匹配岗位话术库。

        Raises:
            TemplateError: 角色不在 ``dev`` / ``pm`` / ``ops`` / ``sales`` 之内。
        """
        try:
            role_enum = Role(role)
        except ValueError as exc:
            allowed = " / ".join(member.value for member in Role)
            raise TemplateError(f"不支持的岗位角色：{role!r}，仅支持 {allowed}") from exc
        return _ROLE_WORDING[role_enum]

    @staticmethod
    def _split_sections(content: str) -> tuple[list[str], dict[str, list[str]]]:
        """按段标记切分正文，返回 ``(段前内容, 各段正文)``。

        Raises:
            TemplateError: 三段标记未全部出现（结构被破坏）。
        """
        preamble: list[str] = []
        sections: dict[str, list[str]] = {name: [] for name in SECTION_ORDER}
        found: set[str] = set()
        current: str | None = None
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            matched = SECTION_PATTERN.match(line)
            if matched:
                current = matched.group("name")
                found.add(current)
                continue
            if current is None:
                # 段标记之前的行（如工作量统计）作为抬头区内容
                preamble.append(line)
                continue
            sections[current].append(line)

        missing = [name for name in SECTION_ORDER if name not in found]
        if missing:
            raise TemplateError(
                "日报正文缺少三段结构标记：" + "、".join(missing) + "（三段结构不可变）"
            )
        return preamble, sections
