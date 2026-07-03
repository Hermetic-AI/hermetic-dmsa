"""梯度样本 / 反馈信号（重导出 dmsa.types，保持模块边界清晰）。

依赖规则：只 import dmsa.types（叶子模块）。
"""

from __future__ import annotations

import os
import struct
import time
from typing import Any, Mapping

from dmsa.types import FeedbackSignal, GradientSample

__all__ = ["GradientSample", "FeedbackSignal", "make_sample", "make_feedback"]


def _new_id() -> str:
    """生成 26 字符的 ID（时间排序 + 随机）。"""
    ts_ms = int(time.time() * 1000)
    ts_bytes = struct.pack(">Q", ts_ms)[2:]  # 6 字节
    ts_part = ts_bytes.hex().upper()[:12]
    rand_part = os.urandom(8).hex().upper()[:16]
    return (ts_part + rand_part)[:26]


def make_sample(
    *,
    feedback_text: str,
    source: str = "USER",
    severity: float = 1.0,
    target_node_id: str = "",
    embedding: tuple[float, ...] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> GradientSample:
    """构造一个 :class:`GradientSample`，自动生成 sample_id。"""
    return GradientSample(
        sample_id=_new_id(),
        feedback_text=feedback_text,
        source=source,
        severity=severity,
        embedding=embedding,
        target_node_id=target_node_id,
        metadata=dict(metadata) if metadata else {},
    )


def make_feedback(
    *,
    target_node_id: str,
    feedback_text: str,
    severity: float = 1.0,
    source: str = "USER",
    extra_context: Mapping[str, Any] | None = None,
) -> FeedbackSignal:
    """构造一个 :class:`FeedbackSignal`，自动生成 feedback_id。"""
    return FeedbackSignal(
        feedback_id=_new_id(),
        target_node_id=target_node_id,
        feedback_text=feedback_text,
        severity=severity,
        source=source,
        extra_context=dict(extra_context) if extra_context else {},
    )
