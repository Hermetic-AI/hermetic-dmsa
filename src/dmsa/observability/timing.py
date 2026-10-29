"""耗时与计数的埋点助手（PD6）。

把 ``time.perf_counter()`` + registry 查找 + 异常计数收敛成一个上下文管理器，
业务侧只写一行，避免每个调用点复制计时代码。

命名约定（沿用仓库既有的点号风格）：

- ``dmsa.<域>.latency``：以秒计的 Histogram。
- ``dmsa.<域>.calls``：该操作的调用次数（含失败）。
- ``dmsa.<域>.failures``：该操作抛异常的次数。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from dmsa.observability.metrics import (
    COUNT_BUCKETS,
    DEFAULT_BUCKETS,
    MetricsRegistry,
    get_registry,
)


def _stem(latency_name: str) -> str:
    """由延迟指标名推出该操作的基名（``dmsa.x.latency`` → ``dmsa.x``）。"""
    return latency_name[: -len(".latency")] if latency_name.endswith(".latency") else latency_name


@contextmanager
def timed(
    latency_name: str,
    *,
    registry: MetricsRegistry | None = None,
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
) -> Iterator[float]:
    """测量一段代码的耗时（秒），并写入 ``latency`` / ``calls`` / ``failures``。

    Args:
        latency_name: 延迟直方图名，约定以 ``.latency`` 结尾。
        registry: 指定 registry（测试用）；None 使用全局注册表。
        buckets: 分桶边界，延迟类默认 :data:`DEFAULT_BUCKETS`。

    Yields:
        本次计时的起始 ``perf_counter`` 值，便于调用方自行取用。
    """
    target = registry if registry is not None else get_registry()
    histogram = target.histogram(latency_name, buckets=buckets)
    base = _stem(latency_name)
    calls = target.counter(f"{base}.calls")
    failures = target.counter(f"{base}.failures")
    started = time.perf_counter()
    try:
        yield started
    except BaseException:
        calls.inc()
        failures.inc()
        raise
    else:
        calls.inc()
        histogram.observe(time.perf_counter() - started)


def observe_count(name: str, value: float, *, registry: MetricsRegistry | None = None) -> None:
    """记录一个个数形态的观测值（自动使用规模桶，避免落进 +Inf）。"""
    target = registry if registry is not None else get_registry()
    target.count_histogram(name).observe(value)


__all__ = [
    "COUNT_BUCKETS",
    "DEFAULT_BUCKETS",
    "observe_count",
    "timed",
]
