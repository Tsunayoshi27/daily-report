"""飞书任务采集器（design.md 4.1 ``collect_feishu``）。

流程：先取 ``tenant_access_token``（缓存至过期前 5 分钟）→ 再调任务列表接口。

行为约束：
1. 请求频率 ≤ 2 次/秒；
2. 3 次指数退避重试，间隔 1s / 2s / 4s；
3. 凭证从 Config 注入（来自 ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``），不硬编码、不落日志；
4. 任务状态按接口字段映射为 ``done`` / ``pending`` / ``blocked``。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from datetime import time as dt_time
from typing import Any, ClassVar

import httpx

from daily_report_tool.collectors.base import Collector
from daily_report_tool.models import TITLE_MAX_LENGTH, RawRecord, RecordStatus, Source
from daily_report_tool.utils.config import Config
from daily_report_tool.utils.exceptions import (
    AuthError,
    ConnectionError,
    RateLimitError,
    TokenExpiredError,
)
from daily_report_tool.utils.retry import retry

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
_PAGE_SIZE = 50
_MAX_PAGES = 20

# 飞书开放平台中与 tenant_access_token 失效相关的错误码
_TOKEN_EXPIRED_CODES = frozenset({99991661, 99991663, 99991664, 99991668})

# 权限不足类错误码：属于永久性错误，重试无意义（重试只会浪费 1s/2s/4s 的时间预算）
_PERMISSION_DENIED_CODES = frozenset({99991672, 99991676, 99991679})

_DONE_WORDS = frozenset({"done", "completed", "complete", "finished", "closed"})
_BLOCKED_WORDS = frozenset({"blocked", "block", "blocked_by_dependency", "risk"})
_PENDING_WORDS = frozenset({"todo", "pending", "open", "doing", "in_progress"})

# 飞书任务 v1 用字符串表示「未完成」
_NOT_COMPLETED = frozenset({"", "0", "none", "null"})


def _day_epoch_bounds(day: date) -> tuple[str, str]:
    """返回当天本地时区的起止秒级时间戳字符串（飞书 v1 接口使用字符串时间戳）。"""
    start = datetime.combine(day, dt_time.min).astimezone()
    end = datetime.combine(day, dt_time.max).astimezone()
    return str(int(start.timestamp())), str(int(end.timestamp()))


def _parse_timestamp(raw: object) -> datetime | None:
    """解析飞书时间字段：支持秒级、毫秒级时间戳字符串与 ISO8601，统一转为本地 naive 时间。"""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        # 13 位为毫秒时间戳
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    """按顺序取第一个非空字段值（兼容飞书不同版本接口的字段命名）。"""
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _map_status(item: dict[str, Any]) -> RecordStatus:
    """把飞书任务状态映射为内部枚举（design.md 4.1：按任务状态字段映射）。"""
    raw = str(item.get("status") or item.get("task_status") or "").strip().lower()
    if raw in _DONE_WORDS:
        return RecordStatus.DONE
    if raw in _BLOCKED_WORDS:
        return RecordStatus.BLOCKED
    if raw in _PENDING_WORDS:
        return RecordStatus.PENDING

    # 无显式状态字段时按完成时间判断：v1 接口字段为 complete_time（秒级，"0" 表示未完成）
    completed_at = str(_first_present(item, "complete_time", "completed_at") or "").strip().lower()
    if completed_at not in _NOT_COMPLETED:
        return RecordStatus.DONE
    # 兜底：自定义字段里带 blocked 标记的视为阻塞
    if "blocked" in str(item.get("extra") or "").lower():
        return RecordStatus.BLOCKED
    return RecordStatus.PENDING


class FeishuCollector(Collector):
    """通过飞书开放平台 API 拉取当天任务记录。"""

    source: ClassVar[Source] = Source.FEISHU

    #: 两次请求的最小间隔（秒），对应 design.md 6.3 的 ≤ 2 次/秒
    MIN_INTERVAL: ClassVar[float] = 0.5
    #: Token 提前刷新的安全余量（秒），对应「缓存至过期前 5 分钟」
    TOKEN_REFRESH_MARGIN: ClassVar[float] = 300.0

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        """初始化采集器。

        Args:
            config: 运行期配置，App ID / Secret 与接口地址均从这里取。
            client: 可选的外部 httpx 客户端（测试时可注入 MockTransport）。
                传入时由调用方负责关闭。
        """
        self._config = config
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._last_request_at = 0.0
        self._token: str | None = None
        self._token_expires_at = 0.0

    def collect(self, day: date) -> list[RawRecord]:
        """实现统一接口，采集指定日期的飞书任务记录。"""
        return self.collect_feishu(day)

    def collect_feishu(self, day: date) -> list[RawRecord]:
        """采集指定日期的飞书任务/消息记录。

        Args:
            day: 采集日期。

        Returns:
            当日任务对应的 RawRecord，``status`` 按任务状态映射。

        Raises:
            AuthError: App ID / Secret 无效。
            ConnectionError: 飞书 API 不可达或返回业务错误。
            TokenExpiredError: Token 刷新后仍失效。
        """
        try:
            return self._collect_pages(day)
        except TokenExpiredError:
            _LOGGER.warning("飞书 tenant_access_token 已过期，强制刷新后重试一次")
            self._token = None
            return self._collect_pages(day)

    def close(self) -> None:
        """关闭内部创建的 HTTP 客户端。"""
        if self._owns_client:
            self._client.close()

    def _collect_pages(self, day: date) -> list[RawRecord]:
        """按页拉取任务列表并转换为 RawRecord。"""
        token = self._get_token()
        start, end = _day_epoch_bounds(day)
        records: list[RawRecord] = []
        seen: set[str] = set()
        page_token = ""
        for _ in range(_MAX_PAGES):
            params = {
                "page_size": str(_PAGE_SIZE),
                "start_create_time": start,
                "end_create_time": end,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._request_json(self._config.feishu_task_path, params, token)
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                _LOGGER.warning("飞书返回结构异常，停止翻页")
                break
            items = data.get("items") or []
            for item in items:
                record = self._to_record(item, day)
                if record is None or record.url in seen:
                    continue
                seen.add(record.url)
                records.append(record)
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more") or not page_token:
                break
        _LOGGER.info("飞书采集完成：共 %d 条记录", len(records))
        return records

    def _get_token(self) -> str:
        """返回可用的 tenant_access_token，命中缓存直接复用。"""
        now = time.monotonic()
        if self._token and now < self._token_expires_at - self.TOKEN_REFRESH_MARGIN:
            return self._token
        self._token = self._fetch_token()
        return self._token

    @retry(max_attempts=4, base_delay=1.0, exceptions=(ConnectionError, RateLimitError))
    def _fetch_token(self) -> str:
        """调用 tenant_access_token 接口获取新 token 并记录过期时间。"""
        self._throttle()
        base = self._config.feishu_base_url.rstrip("/")
        url = f"{base}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._config.feishu_app_id,
            "app_secret": self._config.feishu_app_secret,
        }
        try:
            response = self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ConnectionError(f"飞书鉴权接口不可达（{type(exc).__name__}）") from exc

        if response.status_code in (401, 403):
            raise AuthError(f"飞书 App ID / Secret 无效（HTTP {response.status_code}）")
        if response.status_code >= 500:
            raise ConnectionError(f"飞书鉴权服务端错误（HTTP {response.status_code}）")

        data = self._json_body(response, "飞书鉴权接口")
        if data.get("code") != 0:
            raise AuthError(f"飞书鉴权失败：code={data.get('code')} msg={data.get('msg')}")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise AuthError("飞书鉴权返回空 tenant_access_token")
        expire = data.get("expire") or 7200
        self._token_expires_at = time.monotonic() + float(expire)
        _LOGGER.info("已获取飞书 tenant_access_token，有效期 %s 秒", expire)
        return token

    @retry(max_attempts=4, base_delay=1.0, exceptions=(ConnectionError, RateLimitError))
    def _request_json(self, path: str, params: dict[str, str], token: str) -> dict[str, Any]:
        """调用飞书业务接口并返回 JSON（业务错误码统一映射为项目异常）。"""
        self._throttle()
        url = f"{self._config.feishu_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectionError(f"飞书 API 不可达：{path}（{type(exc).__name__}）") from exc

        if response.status_code in (401, 403):
            raise AuthError(f"飞书接口鉴权失败（HTTP {response.status_code}）：{path}")
        if response.status_code == 429:
            raise RateLimitError(f"飞书 API 触发速率限制：{path}")
        if response.status_code >= 500:
            raise ConnectionError(f"飞书服务端错误（HTTP {response.status_code}）：{path}")

        data = self._json_body(response, path)
        code = int(data.get("code") or 0)
        if code in _TOKEN_EXPIRED_CODES:
            self._token = None
            raise TokenExpiredError(f"飞书 tenant_access_token 失效：code={code}")
        if code in _PERMISSION_DENIED_CODES:
            raise AuthError(
                "飞书应用缺少任务读取权限（需 task:task:read 或 task:task:readonly）："
                f"code={code} msg={data.get('msg')}"
            )
        if code != 0:
            raise ConnectionError(f"飞书业务接口返回错误：code={code} msg={data.get('msg')}")
        return data

    @staticmethod
    def _json_body(response: httpx.Response, context: str) -> dict[str, Any]:
        """解析响应 JSON，非法内容转成 ConnectionError。"""
        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectionError(f"{context} 返回内容不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ConnectionError(f"{context} 返回结构异常（期望 JSON 对象）")
        return data

    def _throttle(self) -> None:
        """保证两次请求间隔不小于 ``MIN_INTERVAL`` 秒（循环校验，规避 sleep 精度不足）。"""
        if self._last_request_at and self.MIN_INTERVAL > 0:
            while True:
                remaining = self.MIN_INTERVAL - (time.monotonic() - self._last_request_at)
                if remaining <= 0:
                    break
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _to_record(self, item: Any, day: date) -> RawRecord | None:
        """把飞书任务结构转换为 RawRecord，无法解析时返回 None。"""
        if not isinstance(item, dict):
            return None
        title = str(item.get("summary") or item.get("title") or "").strip()
        if not title:
            _LOGGER.debug("跳过无标题的飞书记录")
            return None

        parsed = _parse_timestamp(_first_present(item, "create_time", "created_at"))
        if parsed is None:
            timestamp = datetime.combine(day, dt_time.min)
        elif parsed.date() != day:
            return None
        else:
            timestamp = parsed

        guid = str(item.get("guid") or item.get("id") or "")
        url = str(item.get("url") or "")
        if not url and guid:
            url = f"https://applink.feishu.cn/client/task/detail/{guid}"

        return RawRecord(
            source=Source.FEISHU,
            timestamp=timestamp,
            project=str(item.get("project") or "飞书任务"),
            title=title[:TITLE_MAX_LENGTH],
            detail=str(item.get("description") or item.get("desc") or "").strip(),
            status=_map_status(item),
            url=url,
            author=str(item.get("creator_id") or item.get("creator") or "飞书用户"),
        )
