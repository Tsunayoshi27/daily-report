"""空话套话过滤器（design.md 4.2 ``filter_content``）。

规则：
1. 只删除**纯虚话行**（整行去掉套话词后不剩任何实质内容），部分命中不动；
2. 段标记与标题行不参与统计，也不删除；
3. 空话占比 > 30% 时在警告列表中给出提示（供员工自查）。
"""

from __future__ import annotations

import logging
import re

from daily_report_tool.generators.template import SECTION_PATTERN

_LOGGER = logging.getLogger(__name__)

#: 空话占比告警阈值
EMPTY_TALK_RATIO_THRESHOLD = 0.30

_BULLET_PREFIX = re.compile(r"^[-*•·]\s*")
_PUNCTUATION = re.compile(r"[\s，。、；;,\.!！?？~～\"'“”‘’()（）\[\]【】]+")

# 空话套话词库：按长度倒序匹配，先吃掉长短语，避免「继续努力工作」被拆成残留
_EMPTY_PHRASES: tuple[str, ...] = tuple(
    sorted(
        {
            "努力工作",
            "认真负责",
            "积极主动",
            "积极配合",
            "全力以赴",
            "再接再厉",
            "继续努力",
            "高度重视",
            "深入贯彻",
            "全面提升",
            "加强沟通",
            "密切配合",
            "团结协作",
            "不畏困难",
            "勇于担当",
            "尽职尽责",
            "任劳任怨",
            "按部就班",
            "有条不紊",
            "稳扎稳打",
            "总结经验",
            "不断学习",
            "努力提升",
            "严格要求自己",
            "按时完成",
            "保质保量",
            "勤勤恳恳",
            "兢兢业业",
            "积极进取",
            "努力",
            "继续",
            "持续",
            "不断",
            "更加",
            "进一步",
            "认真",
            "积极",
            "主动",
            "踏实",
            "务实",
        },
        key=len,
        reverse=True,
    )
)

# 泛化填充词：剔除套话后仅剩这些词（如「团队团结协作，密切配合」→「团队」）同样视为无实质内容
_FILLER_WORDS: tuple[str, ...] = (
    "团队",
    "工作",
    "任务",
    "事项",
    "方面",
    "情况",
    "能力",
    "水平",
    "自己",
    "大家",
    "同事",
    "部门",
)


def _is_structure_line(text: str) -> bool:
    """判断是否为段标记、Markdown 标题或分组小标题（不参与空话统计）。"""
    return (
        bool(SECTION_PATTERN.match(text))
        or text.startswith("#")
        or text.startswith("**")
        or text.startswith("工作量")
    )


def is_empty_talk(text: str) -> bool:
    """判断整行是否为纯空话套话。

    先剥离项目符号与标点，再剔除全部套话词；若一无所剩即判定为空话。
    因此「持续跟进客户反馈」这类含实质内容的行**不会**被误删。
    """
    body = _PUNCTUATION.sub("", _BULLET_PREFIX.sub("", text))
    if not body:
        return True
    residue = body
    for phrase in _EMPTY_PHRASES:
        residue = residue.replace(phrase, "")
    for filler in _FILLER_WORDS:
        residue = residue.replace(filler, "")
    return not residue


def _tidy(lines: list[str]) -> str:
    """去掉行尾空白、合并连续空行、删除结尾空行。"""
    result: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if not stripped and result and not result[-1]:
            continue
        result.append(stripped)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


class ContentFilter:
    """过滤日报中的纯空话行。"""

    def filter_content(self, content: str) -> tuple[str, list[str]]:
        """过滤空话套话。

        Args:
            content: 待过滤的日报正文。

        Returns:
            ``(过滤后的文本, 警告列表)``；警告列表为空表示无需关注。

        Raises:
            无。任何输入都会被安全处理。
        """
        kept: list[str] = []
        removed: list[tuple[int, str]] = []
        candidates = 0

        for index, raw in enumerate(content.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or _is_structure_line(stripped):
                kept.append(raw)
                continue
            candidates += 1
            if is_empty_talk(stripped):
                removed.append((index, stripped))
                continue
            kept.append(raw)

        warnings: list[str] = []
        ratio = len(removed) / candidates if candidates else 0.0
        if ratio > EMPTY_TALK_RATIO_THRESHOLD:
            warnings.append(f"空话占比 {ratio:.0%}，请检查")
        if removed:
            warnings.extend(f"第 {index} 行疑似空话，已删除：{text}" for index, text in removed[:5])
            if len(removed) > 5:
                warnings.append(f"其余 {len(removed) - 5} 行空话已删除")
            _LOGGER.warning("已删除 %d 行空话套话，占比 %.0f%%", len(removed), ratio * 100)

        return _tidy(kept), warnings
