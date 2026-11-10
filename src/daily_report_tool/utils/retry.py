"""通用指数退避重试装饰器（design.md 6.3 重试策略）。

所有外部 API 调用（GitHub / 飞书 / SMTP）统一使用本装饰器，避免各处重复实现退避逻辑。
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_LOGGER = logging.getLogger(__name__)

DEFAULT_EXCEPTIONS: tuple[type[BaseException], ...] = (Exception,)


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    multiplier: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple[type[BaseException], ...] = DEFAULT_EXCEPTIONS,
    jitter: float = 0.0,
    sleep: Callable[[float], None] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试装饰器（仅用于同步函数）。

    等待序列为 ``base_delay, base_delay*multiplier, ...``（每次不超过 ``max_delay``）。

    Args:
        max_attempts: 最大尝试次数（含首次调用）。默认 3，即在第 3 次失败后抛出原异常。
            需要"首次调用 + 3 次重试、间隔 1s/2s/4s"时传 ``max_attempts=4``。
        base_delay: 首次重试前等待秒数。
        multiplier: 退避倍数。
        max_delay: 单次等待上限秒数。
        exceptions: 触发重试的异常类型元组，默认捕获所有 ``Exception``。
        jitter: 随机抖动上限（秒），用于打散并发重试。
        sleep: 睡眠函数；为 None 时在**调用时**读取 ``time.sleep``，
            便于测试统一替换等待实现（同时仍支持显式注入替身）。

    Returns:
        装饰后的函数。重试耗尽后抛出最后一次捕获的**原异常**。

    Raises:
        ValueError: ``max_attempts`` < 1 或 ``base_delay`` < 0。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须 >= 1")
    if base_delay < 0:
        raise ValueError("base_delay 不能为负数")

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = base_delay
            last_error: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        _LOGGER.error(
                            "%s 第 %d/%d 次尝试失败，放弃重试：%s: %s",
                            func.__qualname__,
                            attempt,
                            max_attempts,
                            type(exc).__name__,
                            exc,
                        )
                        break
                    wait = min(delay, max_delay)
                    if jitter > 0:
                        wait += random.uniform(0, jitter)
                    _LOGGER.warning(
                        "%s 第 %d/%d 次尝试失败（%s: %s），%.2f 秒后重试",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        exc,
                        wait,
                    )
                    (sleep or time.sleep)(wait)
                    delay *= multiplier
            if last_error is None:  # pragma: no cover - 循环未捕获异常时才会命中
                raise RuntimeError("retry 未捕获到异常即退出循环")
            raise last_error

        return wrapper

    return decorator
