"""GitHub 提交记录采集器（design.md 4.1 ``collect_github``）。

行为约束：
1. 请求频率 ≤ 1 次/秒（:meth:`_throttle` 基于 ``time.monotonic()`` 做最小间隔控制）；
2. 3 次指数退避重试，间隔 1s / 2s / 4s（``max_attempts=4`` = 首次 + 3 次重试）；
3. Token 由 :class:`~daily_report_tool.utils.config.Config` 注入，绝不硬编码、绝不落日志；
4. 单个仓库失败不阻断其他仓库（部分成功优于全盘失败），但凭证类错误直接上抛。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, ClassVar

import httpx

from daily_report_tool.collectors.base import Collector, day_window, parse_iso_timestamp
from daily_report_tool.models import TITLE_MAX_LENGTH, RawRecord, RecordStatus, Source
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import AuthError, ConnectionError, RateLimitError
from daily_report_tool.utils.retry import retry

_LOGGER = logging.getLogger(__name__)

_PER_PAGE = 100
_MAX_PAGES = 20
_TIMEOUT_SECONDS = 10.0


class GitHubCollector(Collector):
    """通过 GitHub REST API 拉取指定仓库当天的 commit 记录。"""

    source: ClassVar[Source] = Source.GITHUB

    #: 两次请求之间的最小间隔（秒），对应 design.md 6.3 的 ≤ 1 次/秒
    MIN_INTERVAL: ClassVar[float] = 1.0

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        """初始化采集器。

        Args:
            config: 运行期配置，Token 与仓库列表均从这里取。
            client: 可选的外部 httpx 客户端（测试时可注入 MockTransport）。
                传入时由调用方负责关闭。
        """
        self._config = config
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._last_request_at = 0.0

    def collect(self, day: date) -> list[RawRecord]:
        """实现统一接口，采集配置中全部仓库的当日记录。"""
        return self.collect_github(list(self._config.github_repos), day)

    def collect_github(self, repos: list[str], day: date) -> list[RawRecord]:
        """采集指定仓库列表在指定日期的 commit 记录。

        Args:
            repos: 仓库列表，格式 ``"owner/repo"``。
            day: 采集日期。

        Returns:
            当日全部 commit 对应的 RawRecord，``status`` 固定为 ``done``。

        Raises:
            AuthError: Token 无效或无权限（凭证问题对所有仓库一致，直接上抛）。
        """
        if not repos:
            _LOGGER.warning("未配置 GitHub 采集仓库（GITHUB_REPOS 为空），跳过 GitHub 采集")
            return []
        if not self._config.github_token:
            _LOGGER.warning("未配置 GITHUB_TOKEN，跳过 GitHub 采集（如主用 Gitee 可忽略此提示）")
            return []

        records: list[RawRecord] = []
        seen_urls: set[str] = set()
        valid_repos: list[str] = []
        last_error: ConnectionError | None = None
        for repo in repos:
            if repo.count("/") != 1:
                _LOGGER.warning("仓库格式非法（应为 owner/repo），跳过：%r", repo)
                continue
            valid_repos.append(repo)
            try:
                records.extend(self._collect_repo(repo, day, seen_urls))
            except (AuthError, RateLimitError):
                # 凭证失效与账号级限流对所有仓库一致，立即上抛，避免无意义的重复重试
                raise
            except ConnectionError as exc:
                # 单仓库失败不影响其他仓库（部分成功优于全盘失败）
                _LOGGER.error("仓库 %s 采集失败，已跳过：%s", repo, exc)
                last_error = exc

        if valid_repos and not records and last_error is not None:
            # 所有仓库都不可达时，向上暴露失败，避免上层误判为"今日无提交"
            raise last_error
        _LOGGER.info("GitHub 采集完成：%d 个仓库，共 %d 条记录", len(valid_repos), len(records))
        return records

    def close(self) -> None:
        """关闭内部创建的 HTTP 客户端。"""
        if self._owns_client:
            self._client.close()

    def _collect_repo(self, repo: str, day: date, seen_urls: set[str]) -> list[RawRecord]:
        """采集单个仓库的当日 commit（自动翻页并去重）。"""
        since, until = day_window(day)
        records: list[RawRecord] = []
        for page in range(1, _MAX_PAGES + 1):
            params = {
                "since": since,
                "until": until,
                "per_page": str(_PER_PAGE),
                "page": str(page),
            }
            items = self._request_json(f"/repos/{repo}/commits", params)
            if items is None:  # 404 等不可达资源，已在内部记录告警
                break
            if not isinstance(items, list):
                _LOGGER.warning("GitHub 返回结构异常，停止翻页：repo=%s", repo)
                break
            for item in items:
                record = self._to_record(item, repo, day)
                if record is None or record.url in seen_urls:
                    continue
                seen_urls.add(record.url)
                records.append(record)
            if len(items) < _PER_PAGE:
                break
        else:
            _LOGGER.warning("仓库 %s 提交数超过 %d 页上限，已截断", repo, _MAX_PAGES)
        return records

    @retry(max_attempts=4, base_delay=1.0, exceptions=(ConnectionError, RateLimitError))
    def _request_json(self, path: str, params: dict[str, str]) -> Any | None:
        """发送一次 GET 请求并返回 JSON；404 返回 None，其余异常按 design.md 4.1 映射。"""
        self._throttle()
        url = f"{self._config.github_api_base_url.rstrip('/')}{path}"
        try:
            response = self._client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ConnectionError(f"GitHub API 不可达：{path}（{type(exc).__name__}）") from exc

        status = response.status_code
        if status == 404:
            _LOGGER.warning("GitHub 资源不存在，跳过：%s", path)
            return None
        if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset", "未知")
            raise RateLimitError(f"GitHub API 触发速率限制，重置于 {reset}：{path}")
        if status in (401, 403):
            raise AuthError(
                f"GitHub Token 无效或无权限（HTTP {status}）：{path}。"
                "请检查 .env：GITHUB_TOKEN 需为完整令牌值，行内注释应与值用空格隔开或单独成行"
            )
        if status >= 500:
            raise ConnectionError(f"GitHub 服务端错误（HTTP {status}）：{path}")
        if status >= 400:
            _LOGGER.warning("GitHub 返回 HTTP %s，跳过：%s", status, path)
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectionError(f"GitHub 返回内容不是合法 JSON：{path}") from exc

    def _headers(self) -> dict[str, str]:
        """构造请求头（Token 只出现在请求头中，不打印）。"""
        return {
            "Authorization": f"Bearer {self._config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "daily-report-tool/0.1.0",
        }

    def _throttle(self) -> None:
        """保证两次请求间隔不小于 ``MIN_INTERVAL`` 秒。

        使用循环校验而非单次 sleep：Windows 的 ``time.sleep`` 精度约 15ms，会提前返回。
        """
        if self._last_request_at and self.MIN_INTERVAL > 0:
            while True:
                remaining = self.MIN_INTERVAL - (time.monotonic() - self._last_request_at)
                if remaining <= 0:
                    break
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _to_record(self, item: Any, repo: str, day: date) -> RawRecord | None:
        """把 GitHub commit 结构转换为 RawRecord，无法解析时返回 None。"""
        if not isinstance(item, dict):
            return None
        commit = item.get("commit") or {}
        if not isinstance(commit, dict):
            return None
        message = str(commit.get("message") or "").strip()
        if not message:
            _LOGGER.debug("跳过无提交信息的 commit：repo=%s", repo)
            return None

        commit_author = commit.get("author") or {}
        timestamp = self._parse_timestamp(commit_author.get("date"), repo)
        if timestamp is None or timestamp.date() != day:
            return None

        title, _, detail = message.partition("\n")
        account = item.get("author") or {}
        author = ""
        if isinstance(account, dict):
            author = str(account.get("login") or "")
        author = author or str(commit_author.get("name") or "unknown")

        return RawRecord(
            source=Source.GITHUB,
            timestamp=timestamp,
            project=repo,
            title=title.strip()[:TITLE_MAX_LENGTH],
            detail=detail.strip(),
            # design.md 4.1：GitHub 提交一律视为已完成
            status=RecordStatus.DONE,
            url=str(item.get("html_url") or ""),
            author=author,
        )

    @staticmethod
    def _parse_timestamp(raw: object, repo: str) -> datetime | None:
        """把 ISO8601（含 ``Z``）转换为本地时间（naive）。"""
        text = str(raw or "").strip()
        if not text:
            return None
        parsed = parse_iso_timestamp(text)
        if parsed is None:
            _LOGGER.warning("commit 时间格式无法解析，已跳过：repo=%s value=%r", repo, text)
        return parsed
