"""记忆层职责模块."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from pmd import (
    EdgeType as PMDEdgeType,
)
from pmd import (
    MemoryNode as PMDMemoryNode,
)
from pmd import (
    PromptContext as PMDPromptContext,
)
from pmd import (
    VersionSnapshot,
)

from dmsa.errors import (
    LineageNotFoundError,
)
from dmsa.memory._base import MemoryState
from dmsa.memory.lineage_query import (
    LineageDirection,
    LineageQuery,
    to_pmd_edge_types,
    validate_query,
)
from dmsa.observability import get_logger, get_registry, start_span

_logger = get_logger("dmsa.memory.layer")
_metrics = get_registry()


class MemoryRead(MemoryState):
    """提供记忆层的独立职责实现."""

    def get_node(self, node_id: str) -> PMDMemoryNode:
        """获取节点;不存在抛 :class:`LineageNotFoundError`."""
        with self._lock:
            try:
                return self._dag.get_node(node_id)
            except LineageNotFoundError:
                raise
            except Exception as exc:
                raise LineageNotFoundError(
                    f"node {node_id!r} not found: {exc}",
                    details={"node_id": node_id},
                ) from exc

    def get_ancestors(
        self,
        node_id: str,
        *,
        depth: int | None = None,
        edge_types: Iterable[PMDEdgeType] | None = None,
    ) -> list[PMDMemoryNode]:
        """获取祖先节点(沿 parents 上行)."""
        with self._lock:
            try:
                et = list(edge_types) if edge_types else None
                if depth is not None:
                    return cast(
                        list[PMDMemoryNode],
                        self._dag.get_ancestors(node_id, depth=depth, edge_types=et),
                    )
                return cast(list[PMDMemoryNode], self._dag.get_ancestors(node_id, edge_types=et))
            except LineageNotFoundError:
                raise
            except Exception as exc:
                raise LineageNotFoundError(
                    f"get_ancestors({node_id!r}) failed: {exc}",
                    details={"node_id": node_id, "depth": depth},
                ) from exc

    def get_descendants(
        self,
        node_id: str,
        *,
        depth: int | None = None,
        edge_types: Iterable[PMDEdgeType] | None = None,
    ) -> list[PMDMemoryNode]:
        """获取后代节点(沿 children 下行)."""
        with self._lock:
            try:
                et = list(edge_types) if edge_types else None
                if depth is not None:
                    return cast(
                        list[PMDMemoryNode],
                        self._dag.get_descendants(node_id, depth=depth, edge_types=et),
                    )
                return cast(list[PMDMemoryNode], self._dag.get_descendants(node_id, edge_types=et))
            except LineageNotFoundError:
                raise
            except Exception as exc:
                raise LineageNotFoundError(
                    f"get_descendants({node_id!r}) failed: {exc}",
                    details={"node_id": node_id, "depth": depth},
                ) from exc

    def query_lineage(self, query: LineageQuery) -> list[PMDMemoryNode]:
        """用 :class:`LineageQuery` 描述符查 lineage."""
        validate_query(query)
        with start_span("dmsa.query_lineage", root_id=query.root_id):
            results: list[PMDMemoryNode] = []
            if query.direction in (LineageDirection.UP, LineageDirection.BOTH):
                ancestors = self.get_ancestors(
                    query.root_id,
                    depth=query.depth if query.depth >= 0 else None,
                    edge_types=to_pmd_edge_types(query.edge_types),
                )
                results.extend(ancestors)
            if query.direction in (LineageDirection.DOWN, LineageDirection.BOTH):
                descendants = self.get_descendants(
                    query.root_id,
                    depth=query.depth if query.depth >= 0 else None,
                    edge_types=to_pmd_edge_types(query.edge_types),
                )
                results.extend(descendants)

            # 应用 filters
            if "min_importance" in query.filters:
                threshold = float(query.filters["min_importance"])
                results = [n for n in results if n.importance >= threshold]
            if "tags" in query.filters:
                required_tags = set(query.filters["tags"])
                results = [n for n in results if required_tags.issubset(set(n.tags))]

            # limit
            return results[: query.limit]

    def get_creator(self, node_id: str) -> PMDPromptContext | None:
        """O(1) creator 查找;返回 None 表示没记录."""
        with self._lock:
            return self._dag.get_creator(node_id)

    def snapshot(
        self,
        *,
        description: str = "",
        tag: str | None = None,
    ) -> VersionSnapshot:
        """打点版本."""
        with self._lock:
            return self._dag.snapshot(description=description, tag=tag)

    def rollback(self, version_id: str) -> None:
        """回滚到指定版本."""
        with self._lock:
            self._dag.rollback(version_id)

    def list_versions(self) -> list[VersionSnapshot]:
        """列出所有版本快照."""
        with self._lock:
            return cast(list[VersionSnapshot], self._dag.list_versions())
