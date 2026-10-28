"""基于 ``contextvars`` 的轻量 trace span（PD6）。

用于跨函数 / 跨 async 任务的调用链追踪。零依赖。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# 当前活跃 span（ContextVar 跨 async task 自动隔离）
_current_span: ContextVar[TraceSpan | None] = ContextVar("dmsa_current_span", default=None)


@dataclass
class TraceSpan:
    """单个 trace span。

    Attributes:
        span_id: 唯一 ID。
        name: 语义名（如 ``"dmsa.backward"``）。
        parent_id: 父 span ID；顶层 span 为 None。
        start_time: Unix 时间戳。
        end_time: Unix 时间戳（未结束为 None）。
        attributes: 任意附加属性。
    """

    span_id: str
    name: str
    parent_id: str | None
    start_time: float
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    _children: list[TraceSpan] = field(default_factory=list, repr=False)

    def set_attribute(self, key: str, value: Any) -> None:
        """设置 span 属性。"""
        self.attributes[key] = value

    def end(self) -> None:
        """标记 span 结束。"""
        if self.end_time is None:
            self.end_time = time.time()

    def duration_seconds(self) -> float:
        """获取 span 持续时间；未结束返回当前 elapsed。"""
        end = self.end_time if self.end_time is not None else time.time()
        return end - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """导出为 dict。"""
        return {
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds(),
            "attributes": dict(self.attributes),
            "children": [c.to_dict() for c in self._children],
        }


class TraceSpanContext:
    """``with`` 块形式的 span 上下文管理器。"""

    def __init__(self, name: str, **attributes: Any) -> None:
        """初始化 span 上下文, 记录名称与初始属性。"""
        self._name = name
        self._attributes = attributes
        self._span: TraceSpan | None = None
        self._token: Any = None

    def __enter__(self) -> TraceSpan:
        """进入 span, 创建并挂载到父 span。"""
        parent = get_current_span()
        self._span = TraceSpan(
            span_id=uuid.uuid4().hex,
            name=self._name,
            parent_id=parent.span_id if parent else None,
            start_time=time.time(),
            attributes=dict(self._attributes),
        )
        if parent is not None:
            parent._children.append(self._span)
        self._token = _current_span.set(self._span)
        return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """退出 span, 标记异常并重置当前 span 上下文。"""
        if self._span is not None:
            if exc_type is not None:
                self._span.set_attribute("error", repr(exc_val))
            self._span.end()
        _current_span.reset(self._token)


def start_span(name: str, **attributes: Any) -> TraceSpanContext:
    """开启一个新 span（返回 context manager）。"""
    return TraceSpanContext(name, **attributes)


def get_current_span() -> TraceSpan | None:
    """获取当前活跃 span；顶层返回 None。"""
    return _current_span.get()


def iter_spans() -> Iterator[TraceSpan]:
    """遍历所有已完成的 span（占位 API；v0.x 仅暴露 in-memory 子树）。"""
    current = get_current_span()
    if current is None:
        return
    yield current
    for child in current._children:
        yield from _walk(child)


def _walk(span: TraceSpan) -> Iterator[TraceSpan]:
    """递归遍历 span 树。"""
    yield span
    for child in span._children:
        yield from _walk(child)


__all__ = [
    "TraceSpan",
    "TraceSpanContext",
    "get_current_span",
    "iter_spans",
    "start_span",
]
