"""可观测性（PD6）。

提供结构化 logging、metrics、trace 三件套，全部基于 stdlib 实现，零依赖。
"""

from __future__ import annotations

from dmsa.observability.log_config import (
    add_logging_args,
    apply_cli_logging,
    configure_root,
    setup_logging,
)
from dmsa.observability.log_formatters import JsonFormatter, TextFormatter
from dmsa.observability.logging import JsonLogger, get_logger
from dmsa.observability.metrics import (
    COUNT_BUCKETS,
    DEFAULT_BUCKETS,
    Counter,
    Histogram,
    MetricsRegistry,
    get_registry,
)
from dmsa.observability.timing import observe_count, timed
from dmsa.observability.trace import (
    TraceSpan,
    bind_trace_id,
    current_trace_id,
    get_current_span,
    start_span,
    trace_context,
)

__all__ = [
    "COUNT_BUCKETS",
    "DEFAULT_BUCKETS",
    "Counter",
    "Histogram",
    "JsonFormatter",
    "JsonLogger",
    "MetricsRegistry",
    "TextFormatter",
    "TraceSpan",
    "add_logging_args",
    "apply_cli_logging",
    "bind_trace_id",
    "configure_root",
    "current_trace_id",
    "get_current_span",
    "get_logger",
    "get_registry",
    "observe_count",
    "setup_logging",
    "start_span",
    "timed",
    "trace_context",
]
