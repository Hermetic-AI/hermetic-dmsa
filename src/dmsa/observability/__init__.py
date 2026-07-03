"""可观测性（PD6）。

提供结构化 logging、metrics、trace 三件套，全部基于 stdlib 实现，零依赖。
"""

from __future__ import annotations

from dmsa.observability.logging import get_logger, JsonLogger
from dmsa.observability.metrics import (
    Counter,
    Histogram,
    MetricsRegistry,
    get_registry,
)
from dmsa.observability.trace import TraceSpan, get_current_span, start_span

__all__ = [
    "get_logger",
    "JsonLogger",
    "Counter",
    "Histogram",
    "MetricsRegistry",
    "get_registry",
    "TraceSpan",
    "get_current_span",
    "start_span",
]
