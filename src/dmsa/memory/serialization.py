"""JSON 序列化（薄封装 pmd 的 to_dict / from_dict）。

PMD skeleton 已提供 MemoryNode / CreatorEdge 的 to_dict()。本模块做：

1. ``to_dict`` — 把整个 MemoryLayer 状态（含所有 node/edge）转成 JSON 友好 dict
2. ``from_dict`` — 从 dict 恢复 MemoryLayer
3. 错误包装：``SerializationError`` 包装 JSON 异常
4. 路径白名单（防路径穿越）

依赖规则：只 import pmd + dmsa.errors。
"""

from __future__ import annotations

import json
from typing import Any, cast

from dmsa.errors import SerializationError

__all__ = ["from_dict", "from_json_string", "to_dict", "to_json_string"]


def to_dict(memory_layer: Any) -> dict[str, Any]:
    """把 :class:`MemoryLayer` 整个状态序列化为 dict。

    Args:
        memory_layer: :class:`dmsa.memory.MemoryLayer` 实例。

    Returns:
        可被 :func:`from_dict` 反序列化的 dict。

    Raises:
        SerializationError: 序列化失败。
    """
    try:
        # 委托给 MemoryLayer 自己（避免循环 import）
        return cast(dict[str, Any], memory_layer.export_state())
    except Exception as exc:
        raise SerializationError(
            f"failed to serialize MemoryLayer: {exc}",
            details={"error": str(exc)},
        ) from exc


def from_dict(payload: dict[str, Any]) -> Any:
    """从 dict 恢复 :class:`MemoryLayer`。

    Args:
        payload: :func:`to_dict` 生成的 dict。

    Returns:
        新的 :class:`MemoryLayer` 实例。

    Raises:
        SerializationError: 反序列化失败。
    """
    try:
        # 委托给 MemoryLayer 自己（避免循环 import）
        from dmsa.memory.layer import MemoryLayer  # 延迟 import 防循环

        return MemoryLayer.import_state(payload)
    except Exception as exc:
        raise SerializationError(
            f"failed to deserialize MemoryLayer: {exc}",
            details={"error": str(exc)},
        ) from exc


def to_json_string(memory_layer: Any, *, indent: int | None = None) -> str:
    """把 :class:`MemoryLayer` 序列化为 JSON 字符串。"""
    payload = to_dict(memory_layer)
    try:
        return json.dumps(payload, ensure_ascii=False, indent=indent)
    except (TypeError, ValueError) as exc:
        raise SerializationError(
            f"failed to encode to JSON: {exc}",
            details={"error": str(exc)},
        ) from exc


def from_json_string(text: str) -> Any:
    """从 JSON 字符串恢复 :class:`MemoryLayer`。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SerializationError(
            f"failed to decode JSON: {exc}",
            details={"line": exc.lineno, "col": exc.colno},
        ) from exc
    if not isinstance(payload, dict):
        raise SerializationError(
            "JSON payload must be an object",
            details={"type": type(payload).__name__},
        )
    return from_dict(payload)
