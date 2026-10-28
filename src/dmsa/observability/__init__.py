"""可观测性（PD6）。

提供结构化 logging、metrics、trace 三件套，全部基于 stdlib 实现，零依赖。
"""

from __future__ import annotations

from dmsa.observability.logging import JsonLogger, get_logger
from dmsa.observability.metrics import (
    Counter,
    Histogram,
    MetricsRegistry,
    get_registry,
)
from dmsa.observability.trace import TraceSpan, get_current_span, start_span

__all__ = [
    "Counter",
    "Histogram",
    "JsonLogger",
    "MetricsRegistry",
    "TraceSpan",
    "get_current_span",
    "get_logger",
    "get_registry",
    "start_span",
]
