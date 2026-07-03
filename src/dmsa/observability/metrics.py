"""轻量 metrics 收集（PD6）。

提供 Counter（单调递增）和 Histogram（分桶统计），基于 dict 实现，零依赖。
适用于单元测试 + 简单监控；生产可替换为 prometheus_client。
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


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
            raise ValueError(f"Counter {self.name!r} cannot be decremented")
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        """当前计数值。"""
        return self._value

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "counter",
            "name": self.name,
            "help": self.help,
            "value": self._value,
        }


# 默认 Histogram 桶（指数增长，覆盖 1ms-10s 范围）
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, math.inf,
)


@dataclass
class Histogram:
    """分桶直方图。线程安全。"""

    name: str
    help: str = ""
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    _counts: dict[float, int] = field(default_factory=dict, repr=False)
    _sum: float = field(default=0.0, repr=False)
    _count: int = field(default=0, repr=False)
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
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[bucket] += 1

    def to_dict(self) -> dict[str, Any]:
        """导出为 dict。"""
        with self._lock:
            return {
                "type": "histogram",
                "name": self.name,
                "help": self.help,
                "count": self._count,
                "sum": self._sum,
                "mean": self._sum / self._count if self._count else 0.0,
                "buckets": {str(b): self._counts[b] for b in self.buckets},
            }


class MetricsRegistry:
    """全局 metrics 注册表。"""

    def __init__(self) -> None:
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
        """获取或创建 Histogram。"""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(
                    name=name, help=help, buckets=buckets
                )
            return self._histograms[name]

    def export(self) -> dict[str, list[dict[str, Any]]]:
        """导出所有 metrics。"""
        with self._lock:
            return {
                "counters": [c.to_dict() for c in self._counters.values()],
                "histograms": [h.to_dict() for h in self._histograms.values()],
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
    "Counter",
    "Histogram",
    "MetricsRegistry",
    "DEFAULT_BUCKETS",
    "get_registry",
]


# 抑制 unused import warning
_ = defaultdict
