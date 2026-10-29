"""轻量 metrics 收集（PD6）。

提供 Counter（单调递增）和 Histogram（累积分桶 + 分位数估算），基于 dict 实现，零依赖。
适用于单元测试 + 简单监控；生产可替换为 prometheus_client。

桶的选择取决于观测量的形态：
- :data:`DEFAULT_BUCKETS`：以秒计的延迟（1ms - 10s）。
- :data:`COUNT_BUCKETS`：以"个数"计的规模（条数、算子数、样本数）。
用延迟桶去记整数计数会让所有 ≥10 的观测全落进 ``+Inf`` 桶，分桶等于没有。
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any

from dmsa.errors import MetricValueError


@dataclass
class Counter:
    """单调计数器。线程安全。"""

    name: str
    help: str = ""
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, amount: float = 1.0) -> None:
        """递增指定值。"""
        if amount < 0:
            raise MetricValueError(f"Counter {self.name!r} cannot be decremented")
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        """当前计数值（CPython 下 float 读取原子，不加锁以避免热路径争用）。"""
        return self._value

    def to_dict(self) -> dict[str, Any]:
        """导出计数器为 dict。"""
        with self._lock:
            return {
                "type": "counter",
                "name": self.name,
                "help": self.help,
                "value": self._value,
            }


# 默认 Histogram 桶（延迟形态，单位秒，覆盖 1ms-10s 范围）
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
    5.0,
    10.0,
    math.inf,
)

# 规模形态 Histogram 桶（观测值是整数计数时使用）
COUNT_BUCKETS: tuple[float, ...] = (
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
    math.inf,
)

# 导出的分位数（p50 / p95 / p99）
QUANTILES: tuple[tuple[str, float], ...] = (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))


def _finite_or_none(value: float) -> float | None:
    """把 ``inf`` / ``nan`` 归一成 ``None``，避免 JSON 出现非标准字面量。"""
    return value if math.isfinite(value) else None


@dataclass
class Histogram:
    """累积分桶直方图 + 分位数估算。线程安全。"""

    name: str
    help: str = ""
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    _counts: dict[float, int] = field(default_factory=dict, repr=False)
    _sum: float = field(default=0.0, repr=False)
    _count: int = field(default=0, repr=False)
    _min: float = field(default=math.inf, repr=False)
    _max: float = field(default=-math.inf, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        """初始化桶计数。"""
        for bucket in self.buckets:
            self._counts.setdefault(bucket, 0)

    def observe(self, value: float) -> None:
        """记录一次观测值。"""
        with self._lock:
            self._sum += value
            self._count += 1
            self._min = min(self._min, value)
            self._max = max(self._max, value)
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[bucket] += 1

    def quantile(self, q: float) -> float | None:
        """估算分位数（桶内线性插值，与 Prometheus ``histogram_quantile`` 同构）。

        Args:
            q: 0.0-1.0 之间的分位点。

        Returns:
            估算值；无观测、分位越界、或落在 ``+Inf`` 桶（超出量程）时返回 None。
        """
        if not 0.0 <= q <= 1.0:
            raise MetricValueError(f"Histogram {self.name!r} quantile out of range: {q}")
        with self._lock:
            return self._quantile_locked(q)

    def _quantile_locked(self, q: float) -> float | None:
        """在持锁状态下做累积计数扫描。"""
        if self._count == 0:
            return None
        rank = q * self._count
        lower = 0.0
        lower_cum = 0
        for bound in self.buckets:
            cumulative = self._counts[bound]
            if cumulative >= rank:
                if math.isinf(bound):
                    return None
                in_bucket = cumulative - lower_cum
                if in_bucket <= 0:
                    return bound
                fraction = (rank - lower_cum) / in_bucket
                return lower + (bound - lower) * fraction
            lower = bound
            lower_cum = cumulative
        return None

    def to_dict(self) -> dict[str, Any]:
        """导出为 dict；未观测到的统计量以 ``None`` 表示，不用 0 冒充。"""
        with self._lock:
            observed = self._count > 0
            stats: dict[str, Any] = {
                "type": "histogram",
                "name": self.name,
                "help": self.help,
                "count": self._count,
                "sum": self._sum,
                "mean": (self._sum / self._count) if observed else None,
                "min": self._min if observed else None,
                "max": self._max if observed else None,
            }
            for key, q in QUANTILES:
                raw = self._quantile_locked(q) if observed else None
                stats[key] = _finite_or_none(raw) if raw is not None else None
            stats["buckets"] = {str(b): self._counts[b] for b in self.buckets}
            return stats


class MetricsRegistry:
    """全局 metrics 注册表。"""

    def __init__(self) -> None:
        """初始化全局 metrics 注册表, 创建计数器和直方图容器。"""
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help: str = "") -> Counter:
        """获取或创建 Counter。"""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name=name, help=help)
            return self._counters[name]

    def histogram(
        self,
        name: str,
        help: str = "",
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> Histogram:
        """获取或创建 Histogram（延迟类观测用默认桶）。"""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name=name, help=help, buckets=buckets)
            return self._histograms[name]

    def count_histogram(self, name: str, help: str = "") -> Histogram:
        """获取或创建规模类 Histogram（观测值是整数个数时用，避免落进 +Inf 桶）。"""
        return self.histogram(name, help, buckets=COUNT_BUCKETS)

    def export(self) -> dict[str, list[dict[str, Any]]]:
        """导出所有 metrics。"""
        with self._lock:
            counters = [self._counters[key] for key in sorted(self._counters)]
            histograms = [self._histograms[key] for key in sorted(self._histograms)]
        return {
            "counters": [c.to_dict() for c in counters],
            "histograms": [h.to_dict() for h in histograms],
        }

    def reset(self) -> None:
        """清空所有 metrics（仅测试用）。"""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


# 全局默认 registry
_global_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    """获取全局 metrics registry。"""
    return _global_registry


__all__ = [
    "COUNT_BUCKETS",
    "DEFAULT_BUCKETS",
    "QUANTILES",
    "Counter",
    "Histogram",
    "MetricsRegistry",
    "get_registry",
]
