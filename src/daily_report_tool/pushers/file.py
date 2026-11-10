"""Markdown / Word 文件导出（design.md 4.3 ``export_file``，ADR-006）。

- ``md``：直接写 UTF-8 文本；
- ``docx``：用 python-docx 生成，Markdown 标题映射为文档标题，``- `` 条目映射为项目符号；
- 文件名严格为 ``日报_{date}_{author}.{ext}``。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from docx import Document

from daily_report_tool.models import DailyReport
from daily_report_tool.utils.config import Config

_LOGGER = logging.getLogger(__name__)

#: 支持的导出格式
SUPPORTED_FORMATS: frozenset[str] = frozenset({"md", "docx"})

# Windows / POSIX 文件名非法字符
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


class FileExporter:
    """把日报导出为 Markdown 或 Word 文件。"""

    def __init__(self, config: Config) -> None:
        """初始化导出器。

        Args:
            config: 运行期配置，导出目录取 ``config.output_dir``。
        """
        self._output_dir = Path(config.output_dir)

    def export_file(self, report: DailyReport, format: str) -> Path:
        """导出日报文件。

        Args:
            report: 待导出的日报实体（使用 ``report.content`` 作为正文）。
            format: 导出格式，``"md"`` 或 ``"docx"``（大小写不敏感）。

        Returns:
            导出文件的完整路径。

        Raises:
            ValueError: 不支持的导出格式。
            IOError: 输出目录不可写或文件写入失败。
        """
        suffix = (format or "").strip().lower()
        if suffix not in SUPPORTED_FORMATS:
            allowed = " / ".join(sorted(SUPPORTED_FORMATS))
            raise ValueError(f"不支持的导出格式：{format!r}，仅支持 {allowed}")

        path = self._output_dir / self._build_filename(report, suffix)
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            if suffix == "md":
                path.write_text(report.content, encoding="utf-8")
            else:
                self._write_docx(path, report)
        except OSError as exc:
            raise OSError(f"导出文件失败（目录不可写？）：{path}（{exc}）") from exc

        _LOGGER.info("日报已导出：%s", path)
        return path.resolve()

    @staticmethod
    def _build_filename(report: DailyReport, suffix: str) -> str:
        """按 ``日报_{date}_{author}.{ext}`` 生成文件名，替换非法字符避免写入失败。"""
        author = _ILLEGAL_FILENAME_CHARS.sub("_", report.author).strip() or "未知作者"
        return f"日报_{report.date.isoformat()}_{author}.{suffix}"

    @staticmethod
    def _write_docx(path: Path, report: DailyReport) -> None:
        """把 Markdown 风格正文写入 Word 文档。"""
        document = Document()
        for raw in report.content.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("## "):
                document.add_heading(stripped[3:].strip(), level=2)
            elif stripped.startswith("# "):
                document.add_heading(stripped[2:].strip(), level=1)
            elif stripped.startswith("- "):
                document.add_paragraph(stripped[2:].strip(), style="List Bullet")
            else:
                document.add_paragraph(stripped)
        # python-docx 只接受字符串路径或文件对象
        document.save(str(path))
