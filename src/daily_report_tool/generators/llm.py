"""LLM 日报生成器（design.md 4.2 ``generate_report``，ADR-004）。

v1 默认 ``provider="mock"``：不依赖 GPU / 网络，直接用记录摘要拼出三段正文；
传 ``provider="ollama"`` 即切换到本地私有化模型（数据不出本机）。

降级策略（design.md 6.1）：LLM 超时（默认 30 秒）/ 不可达 / 返回空内容 / 返回内容不符合
三段结构时，**不中断流程**，记录警告后自动降级为纯模板方案（直接拼接记录摘要），
保证「日报总能产出」。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence

import httpx

from daily_report_tool.generators.template import (
    SECTION_DONE,
    SECTION_ORDER,
    SECTION_PATTERN,
    SECTION_PLAN,
    SECTION_RISK,
)
from daily_report_tool.models import RawRecord
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import (
    EmptyResponseError,
    LLMConnectionError,
    LLMTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

VALID_PROVIDERS = frozenset({"mock", "ollama"})

#: assemble_report 的分组键 → 段标题
_GROUP_SECTION: tuple[tuple[str, str], ...] = (
    ("done", SECTION_DONE),
    ("pending", SECTION_PLAN),
    ("blocked", SECTION_RISK),
)

#: 无数据源时的固定输出（proposal 成功标准：虚假内容次数 = 0）
NO_DATA_TEXT = "今日无记录"

_PROMPT_RULES = """你是企业内部工作日报撰写助手，负责把采集到的工作记录整理成日报正文。

硬性约束（必须严格遵守）：
1. 只能使用下方列出的工作记录，禁止编造、推断、夸大或补充任何未出现的工作内容；
2. 某一段没有任何记录时，该段正文只输出 "- 无"，不得编造内容填充；
3. 输出必须严格包含三段，段标题分别为【今日完成】【明日计划】【风险阻塞】，顺序不可调整；
4. 每段内如有本人记录，先输出小标题 "**我的工作（N 条）**" 再逐条列；如有他人记录，
   随后输出小标题 "**团队其他成员（N 条）**" 再逐条列；某组为空则不输出该小标题；
5. 每条记录占一行，以 "- " 开头，保留记录中的项目名与具体事实；他人记录需在末尾补充作者；
6. 第一段之前单独输出一行工作量统计，格式为 "工作量：本人 X 条 / 团队合计 Y 条（Z 人）"；
7. 不要输出开场白、结尾总结、寒暄或评价性套话（如“努力工作”“认真负责”）。
"""


def _one_line(text: str, limit: int = 120) -> str:
    """把多行细节压成单行并做长度截断。"""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _mock_line(record: RawRecord, with_author: bool = False) -> str:
    """Mock provider 的记录摘要：标题 + 细节 + 项目 + 时间（他人记录附带作者）。"""
    stamp = record.timestamp.strftime("%H:%M")
    tail = f"{record.project} · {stamp}"
    if with_author:
        tail = f"{tail} · {record.author}"
    if record.detail:
        return f"- {record.title}：{_one_line(record.detail)}（{tail}）"
    return f"- {record.title}（{tail}）"


def _template_line(record: RawRecord, with_author: bool = False) -> str:
    """纯模板方案的最简拼接：标题 + 项目（他人记录附带作者）。"""
    suffix = f" · {record.author}" if with_author else ""
    return f"- {record.title}（{record.project}{suffix}）"


def _has_all_sections(text: str) -> bool:
    """判断 LLM 返回内容是否包含完整三段结构。"""
    found = {
        matched.group("name")
        for matched in (SECTION_PATTERN.match(line.strip()) for line in text.splitlines())
        if matched is not None
    }
    return found >= set(SECTION_ORDER)


class LLMGenerator:
    """把分组后的原始记录生成三段式日报正文。"""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        """初始化生成器。

        Args:
            config: 运行期配置，读取 ``llm_provider`` / ``llm_base_url`` / ``llm_model`` /
                ``llm_timeout``。
            client: 可选的外部 httpx 客户端（测试注入 MockTransport 用）。

        Raises:
            ValueError: ``llm_provider`` 不是 ``mock`` 或 ``ollama``。
        """
        provider = (config.llm_provider or "").strip().lower()
        if provider not in VALID_PROVIDERS:
            raise ValueError(f"不支持的 LLM provider：{provider!r}（仅支持 mock / ollama）")
        self._config = config
        self._client = client
        self.provider = provider
        #: 本次生成是否降级，以及降级原因（供编排层记录日志）
        self.degraded_reason: str | None = None
        #: 用于判定"我"的账号别名集合（Gitee/GitHub 登录名或昵称，大小写不敏感）
        self._identities = self._build_identities(config)

    @staticmethod
    def _build_identities(config: Config) -> set[str]:
        """构造身份别名集合：收录 ``REPORT_IDENTITIES`` 与 ``REPORT_AUTHOR``。"""
        names = {*config.report_identities, config.report_author}
        return {name.strip().lower() for name in names if name and name.strip()}

    def _is_mine(self, record: RawRecord) -> bool:
        """判断该记录是否由日报作者本人产出。"""
        return record.author.strip().lower() in self._identities

    def generate_report(
        self, records_by_status: Mapping[str, Sequence[RawRecord]], author: str, role: str
    ) -> str:
        """生成日报正文（含三段结构）。

        Args:
            records_by_status: :meth:`ReportAssembler.assemble_report` 的输出
                （``{"done": [...], "pending": [...], "blocked": [...]}``）。
            author: 日报作者。
            role: 岗位角色。

        Returns:
            三段式日报正文；无任何记录时返回 ``"今日无记录"``，绝不编造内容。

        Raises:
            无。LLM 超时 / 不可达 / 返回异常时自动降级，异常原因记录在
            :attr:`degraded_reason`。
        """
        self.degraded_reason = None

        if self._is_empty(records_by_status):
            _LOGGER.info("无任何原始记录，按规范输出“%s”，不编造内容", NO_DATA_TEXT)
            return self._empty_body()

        if self.provider == "mock":
            _LOGGER.info("使用 Mock provider 生成日报：作者=%s 岗位=%s", author, role)
            return self._render_body(records_by_status, _mock_line)

        prompt = self._build_prompt(records_by_status, author, role)
        try:
            body = self._call_llm(prompt)
        except (LLMTimeoutError, LLMConnectionError, EmptyResponseError) as exc:
            self.degraded_reason = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("LLM 生成失败，降级为纯模板方案：%s", exc)
            return self._render_body(records_by_status, _template_line)

        if not _has_all_sections(body):
            self.degraded_reason = "LLM 返回内容缺少三段结构标记"
            _LOGGER.warning("LLM 返回内容不符合三段结构，降级为纯模板方案")
            return self._render_body(records_by_status, _template_line)
        return body

    @staticmethod
    def _is_empty(records_by_status: Mapping[str, Sequence[RawRecord]]) -> bool:
        """三组素材是否全为空。"""
        return not any(records_by_status.get(key) for key, _ in _GROUP_SECTION)

    def _render_body(
        self,
        records_by_status: Mapping[str, Sequence[RawRecord]],
        line_builder: Callable[..., str],
    ) -> str:
        """按固定三段结构拼接正文：段内区分「我的工作」与「团队其他成员」，空段写 ``- 无``。"""
        blocks: list[str] = []
        for key, section in _GROUP_SECTION:
            items = list(records_by_status.get(key) or [])
            lines = [f"【{section}】"]
            if not items:
                lines.append("- 无")
                blocks.append("\n".join(lines))
                continue
            mine = [item for item in items if self._is_mine(item)]
            others = [item for item in items if not self._is_mine(item)]
            if mine:
                lines.append(f"**我的工作（{len(mine)} 条）**")
                lines.extend(line_builder(item, False) for item in mine)
            if others:
                lines.append(f"**团队其他成员（{len(others)} 条）**")
                lines.extend(line_builder(item, True) for item in others)
            blocks.append("\n".join(lines))
        return f"{self._workload_line(records_by_status)}\n\n" + "\n\n".join(blocks)

    def _workload_line(self, records_by_status: Mapping[str, Sequence[RawRecord]]) -> str:
        """生成工作量统计行：本人条数 / 团队合计 / 参与人数。"""
        records = [
            record for key, _ in _GROUP_SECTION for record in (records_by_status.get(key) or [])
        ]
        mine = [record for record in records if self._is_mine(record)]
        teammate_authors = {
            record.author for record in records if not self._is_mine(record) and record.author
        }
        if teammate_authors:
            return (
                f"工作量：本人 {len(mine)} 条 / 团队合计 {len(records)} 条"
                f"（其他 {len(teammate_authors)} 人）"
            )
        return f"工作量：本人 {len(mine)} 条"

    @staticmethod
    def _empty_body() -> str:
        """无数据时的正文：显式声明「今日无记录」，三段均为「无」。"""
        blocks = [f"【{section}】\n- 无" for section in SECTION_ORDER]
        return f"{NO_DATA_TEXT}\n\n" + "\n\n".join(blocks)

    def _build_prompt(
        self, records_by_status: Mapping[str, Sequence[RawRecord]], author: str, role: str
    ) -> str:
        """构造 LLM Prompt（强制约束"无数据禁编造"，并标注记录归属）。"""
        parts = [_PROMPT_RULES, f"【作者】{author}", f"【岗位】{role}", "【待整理记录】"]
        for key, section in _GROUP_SECTION:
            items = list(records_by_status.get(key) or [])
            parts.append(f"【{section}】（{len(items)} 条）")
            if not items:
                parts.append("- 无")
                continue
            for record in items:
                stamp = record.timestamp.strftime("%Y-%m-%d %H:%M")
                detail = f"；{_one_line(record.detail)}" if record.detail else ""
                owner = "本人" if self._is_mine(record) else f"团队·{record.author}"
                parts.append(f"- [{owner}][{record.project}][{stamp}] {record.title}{detail}")
        parts.append("请按约束输出三段式日报正文。")
        return "\n".join(parts)

    def _call_llm(self, prompt: str) -> str:
        """调用 Ollama 本地模型。

        Raises:
            LLMTimeoutError: 超过 ``llm_timeout``（默认 30 秒）。
            LLMConnectionError: 服务不可达或返回非 JSON。
            EmptyResponseError: 返回内容为空。
        """
        url = f"{self._config.llm_base_url.rstrip('/')}/api/generate"
        payload = {
            "model": self._config.llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload)
            else:
                with httpx.Client(timeout=self._config.llm_timeout) as client:
                    response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"LLM 调用超时（{self._config.llm_timeout:.0f} 秒）") from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"LLM 服务不可达：{type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise LLMConnectionError(f"LLM 服务返回 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMConnectionError("LLM 返回内容不是合法 JSON") from exc

        text = str(data.get("response") or "").strip()
        if not text:
            raise EmptyResponseError("LLM 返回内容为空")
        return text
