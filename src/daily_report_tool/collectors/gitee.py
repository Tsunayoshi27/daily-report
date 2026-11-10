"""Gitee 提交记录采集器（proposal 3.1「代码提交记录」的 Gitee 实现）。

Gitee OpenAPI v5 的提交列表接口与 GitHub 基本同构：

    GET {GITEE_API_BASE_URL}/repos/{owner}/{repo}/commits?since=&until=&page=&per_page=

与 GitHub 的关键差异：
1. 鉴权走 **URL query 参数** ``access_token``（GitHub 走 ``Authorization`` 头）；
2. 公开仓库可**匿名读取**，因此 token 允许留空；
3. 未公开明确限速值，本项目沿用保守的 ≤1 次/秒 + 3 次指数退避重试。

安全说明：token 会出现在 URL 中，日志层已由 ``SensitiveDataFilter`` 统一脱敏为
``access_token=***``（已实测验证）。
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


class GiteeCollector(Collector):
    """通过 Gitee OpenAPI v5 拉取指定仓库当天的 commit 记录。"""

    source: ClassVar[Source] = Source.GITEE

    #: 两次请求之间的最小间隔（秒）；Gitee 未公开限速值，沿用与 GitHub 相同的保守值
    MIN_INTERVAL: ClassVar[float] = 1.0

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        """初始化采集器。

        Args:
            config: 运行期配置，Token 与仓库列表均从这里取（Token 可为空，公开仓库免鉴权）。
            client: 可选的外部 httpx 客户端（测试时可注入 MockTransport）。
        """
        self._config = config
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._last_request_at = 0.0

    def collect(self, day: date) -> list[RawRecord]:
        """实现统一接口，采集配置中全部仓库的当日记录。"""
        return self.collect_gitee(list(self._config.gitee_repos), day)

    def collect_gitee(self, repos: list[str], day: date) -> list[RawRecord]:
        """采集指定仓库列表在指定日期的 commit 记录。

        Args:
            repos: 仓库列表，格式 ``"owner/repo"``。
            day: 采集日期。

        Returns:
            当日全部 commit 对应的 RawRecord，``status`` 固定为 ``done``。

        Raises:
            AuthError: Token 无效或无权访问仓库。
            RateLimitError: 触发 Gitee 频率限制。
        """
        if not repos:
            _LOGGER.warning("未配置 Gitee 采集仓库（GITEE_REPOS 为空），跳过 Gitee 采集")
            return []

        records: list[RawRecord] = []
        seen_urls: set[str] = set()
        valid_repos: list[str] = []
        last_error: ConnectionError | None = None
        for repo in repos:
            if repo.count("/") != 1:
                _LOGGER.warning("Gitee 仓库格式非法（应为 owner/repo），跳过：%r", repo)
                continue
            valid_repos.append(repo)
            try:
                records.extend(self._collect_repo(repo, day, seen_urls))
            except (AuthError, RateLimitError):
                # 凭证失效与限流对所有仓库一致，立即上抛，避免无意义的重复重试
                raise
            except ConnectionError as exc:
                _LOGGER.error("Gitee 仓库 %s 采集失败，已跳过：%s", repo, exc)
                last_error = exc

        if valid_repos and not records and last_error is not None:
            raise last_error
        _LOGGER.info("Gitee 采集完成：%d 个仓库，共 %d 条记录", len(valid_repos), len(records))
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
            if items is None:  # 404 等不可达资源
                break
            if not isinstance(items, list):
                _LOGGER.warning("Gitee 返回结构异常，停止翻页：repo=%s", repo)
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
            _LOGGER.warning("Gitee 仓库 %s 提交数超过 %d 页上限，已截断", repo, _MAX_PAGES)
        return records

    @retry(max_attempts=4, base_delay=1.0, exceptions=(ConnectionError, RateLimitError))
    def _request_json(self, path: str, params: dict[str, str]) -> Any | None:
        """发送一次 GET 请求并返回 JSON；404 返回 None，其余异常按 design.md 4.1 映射。"""
        self._throttle()
        url = f"{self._config.gitee_api_base_url.rstrip('/')}{path}"
        query = dict(params)
        if self._config.gitee_token:
            # Gitee 的鉴权方式是 query 参数，不是 Authorization 头
            query["access_token"] = self._config.gitee_token
        try:
            response = self._client.get(url, params=query)
        except httpx.HTTPError as exc:
            raise ConnectionError(f"Gitee API 不可达：{path}（{type(exc).__name__}）") from exc

        status = response.status_code
        if status == 404:
            _LOGGER.warning("Gitee 仓库不存在或无权限，跳过：%s", path)
            return None
        if status == 429:
            raise RateLimitError(f"Gitee API 触发速率限制：{path}")
        if status in (401, 403):
            raise AuthError(
                f"Gitee Token 无效或无权限（HTTP {status}）：{path}。"
                "请检查 .env：GITEE_TOKEN 需为完整令牌值，行内注释应与值用空格隔开或单独成行"
            )
        if status >= 500:
            raise ConnectionError(f"Gitee 服务端错误（HTTP {status}）：{path}")
        if status >= 400:
            _LOGGER.warning("Gitee 返回 HTTP %s，跳过：%s", status, path)
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectionError(f"Gitee 返回内容不是合法 JSON：{path}") from exc

    def _throttle(self) -> None:
        """保证两次请求间隔不小于 ``MIN_INTERVAL`` 秒（循环校验，规避 sleep 精度不足）。"""
        if self._last_request_at and self.MIN_INTERVAL > 0:
            while True:
                remaining = self.MIN_INTERVAL - (time.monotonic() - self._last_request_at)
                if remaining <= 0:
                    break
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _to_record(self, item: Any, repo: str, day: date) -> RawRecord | None:
        """把 Gitee commit 结构转换为 RawRecord，无法解析时返回 None。"""
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
            author = str(account.get("login") or account.get("name") or "")
        author = author or str(commit_author.get("name") or "unknown")

        return RawRecord(
            source=Source.GITEE,
            timestamp=timestamp,
            project=repo,
            title=title.strip()[:TITLE_MAX_LENGTH],
            detail=detail.strip(),
            # 代码提交一律视为已完成
            status=RecordStatus.DONE,
            url=str(item.get("html_url") or ""),
            author=author,
        )

    @staticmethod
    def _parse_timestamp(raw: object, repo: str) -> datetime | None:
        """解析提交时间，失败时记录告警。"""
        parsed = parse_iso_timestamp(raw)
        if parsed is None and str(raw or "").strip():
            _LOGGER.warning("Gitee commit 时间格式无法解析，已跳过：repo=%s value=%r", repo, raw)
        return parsed
