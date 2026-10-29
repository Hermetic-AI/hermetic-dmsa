"""记忆层职责模块."""

from __future__ import annotations

import time

from pmd import (
    MemoryNode as PMDMemoryNode,
)
from pmd.core.enums import NodeStatus as PMDNodeStatus

from dmsa.errors import (
    MemoryLayerError,
)
from dmsa.memory._base import MemoryState
from dmsa.observability import get_logger

_logger = get_logger("dmsa.memory.layer")


class MemorySearch(MemoryState):
    """提供记忆层的独立职责实现."""

    def retrieve(
        self,
        query: str,
        *,
        k: int = 10,
    ) -> list[PMDMemoryNode]:
        """纯向量检索(需要 embedder)."""
        with self._lock:
            if self._embedder is None:
                raise MemoryLayerError(
                    "retrieve() requires an embedder to be attached",
                    details={"hint": "pass embedder= when constructing MemoryLayer"},
                )
            return self._active_lineage_search(
                query,
                k=k,
                depth=0,  # 纯向量,不展开 lineage
            )

    def lineage_aware_search(
        self,
        query: str,
        *,
        k: int = 10,
        depth: int = 2,
        alpha: float = 0.6,
        beta: float = 0.3,
        gamma: float = 0.1,
    ) -> list[PMDMemoryNode]:
        """lineage-aware 检索(需要 embedder)."""
        with self._lock:
            if self._embedder is None:
                raise MemoryLayerError(
                    "lineage_aware_search requires an embedder",
                    details={"hint": "pass embedder= when constructing MemoryLayer"},
                )
            return self._active_lineage_search(
                query,
                k=k,
                depth=depth,
                alpha=alpha,
                beta=beta,
                gamma=gamma,
            )

    def _active_lineage_search(
        self,
        query: str,
        *,
        k: int,
        depth: int,
        alpha: float = 0.6,
        beta: float = 0.3,
        gamma: float = 0.1,
    ) -> list[PMDMemoryNode]:
        """在初始候选和 lineage 扩展阶段过滤不可检索节点."""
        from pmd.core.constants import DEFAULT_MAX_DEPTH
        from pmd.core.enums import EdgeType
        from pmd.core.operations import _cosine, _min_graph_distance

        if k <= 0:
            return []
        now = time.time()
        active = {
            node_id: node
            for node_id, node in self._dag._nodes.items()
            if node.status == PMDNodeStatus.ACTIVE
            and (node.ttl is None or node.created_at + node.ttl > now)
        }
        q_vec = self._embedder.embed(query)
        candidates = {
            node_id: (_cosine(node.embedding, q_vec), node)
            for node_id, node in active.items()
            if node.embedding is not None
        }
        top = sorted(candidates.values(), key=lambda item: item[0], reverse=True)[: 2 * k]
        seed_ids = {node.id for _, node in top}
        candidate_ids = set(seed_ids)
        if depth > 0:
            for seed_id in seed_ids:
                candidate_ids.update(
                    node.id
                    for node in self._dag.get_ancestors(
                        seed_id,
                        depth=depth,
                        edge_types={EdgeType.DERIVED_FROM, EdgeType.CREATED_FROM},
                    )
                    if node.id in active
                )
        for node_id in candidate_ids - candidates.keys():
            candidates[node_id] = (0.0, active[node_id])
        scored = []
        for node_id, (cosine, node) in candidates.items():
            if node_id not in candidate_ids:
                continue
            distance = _min_graph_distance(node_id, seed_ids, self._dag)
            proximity = 1.0 / (1.0 + distance) if distance < DEFAULT_MAX_DEPTH + 1 else 0.0
            scored.append((alpha * cosine + beta * proximity + gamma * node.importance, node))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in scored[:k]]
