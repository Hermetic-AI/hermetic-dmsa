"""LineageQuery 构造 + 转换。

封装 PMD 的节点查询参数：

- :class:`LineageDirection` — UP / DOWN / BOTH
- :class:`LineageQuery` — 不可变查询描述（frozen dataclass）
- :func:`to_pmd_edge_types` — 转换为 pmd.EdgeType set

依赖规则：只 import pmd + dmsa.errors。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pmd import EdgeType as PMDEdgeType

from dmsa.errors import LineageNotFoundError

__all__ = [
    "LineageDirection",
    "LineageQuery",
    "to_pmd_edge_types",
    "validate_query",
]


class LineageDirection(str, Enum):
    """lineage 遍历方向。"""

    UP = "UP"  # 沿 parents 上行
    DOWN = "DOWN"  # 沿 children 下行
    BOTH = "BOTH"  # 双向（默认）


@dataclass(frozen=True, slots=True)
class LineageQuery:
    """lineage 查询描述符。

    Attributes:
        root_id: 起始节点 ULID。
        depth: 最大深度；``-1`` 表示不限制（实际 cap 在 PMD 内置）。
        direction: 遍历方向。
        edge_types: 边类型集合；默认 ``{DERIVED_FROM, CREATED_FROM}``。
        filters: 节点过滤条件（``min_importance``、``tags``、``after``）。
        limit: 返回节点数上限。
    """

    root_id: str
    depth: int = 2
    direction: LineageDirection = LineageDirection.BOTH
    edge_types: frozenset[PMDEdgeType] = field(
        default_factory=lambda: frozenset(
            {PMDEdgeType.DERIVED_FROM, PMDEdgeType.CREATED_FROM}
        )
    )
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = 1000


def to_pmd_edge_types(
    edge_types: frozenset[PMDEdgeType] | None,
) -> list[PMDEdgeType] | None:
    """把 frozenset 转成 pmd 期望的 list | None。"""
    if edge_types is None:
        return None
    return list(edge_types)


def validate_query(query: LineageQuery) -> None:
    """校验 LineageQuery 字段。"""
    if not query.root_id:
        raise LineageNotFoundError(
            "LineageQuery.root_id is empty",
            details={"query": str(query)},
        )
    if query.depth < -1:
        raise LineageNotFoundError(
            f"LineageQuery.depth must be >= -1, got {query.depth}",
            details={"depth": query.depth},
        )
    if query.limit < 1:
        raise LineageNotFoundError(
            f"LineageQuery.limit must be >= 1, got {query.limit}",
            details={"limit": query.limit},
        )
