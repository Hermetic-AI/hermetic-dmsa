"""记忆层职责模块."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pmd import (
    CreatorEdge,
)
from pmd import (
    MemoryNode as PMDMemoryNode,
)

from dmsa.errors import (
    MemoryLayerError,
)
from dmsa.memory._base import MemoryState
from dmsa.observability import get_logger
from dmsa.types import MemoryLayerConfig

if TYPE_CHECKING:
    from dmsa.memory.layer import MemoryLayer

_logger = get_logger("dmsa.memory.layer")


class MemorySerialization(MemoryState):
    """提供记忆层的独立职责实现."""

    def export_state(self) -> dict[str, Any]:
        """把当前 MemoryLayer 状态导出为 dict."""
        with self._lock:
            nodes = {nid: n.model_dump(mode="json") for nid, n in self._dag._nodes.items()}
            edges = {
                f"{e.from_node_id}|{e.to_node_id}|{e.edge_type.value}": e.model_dump(mode="json")
                for e in self._dag._edges.values()
            }
            return {
                "schema": "dmsa.memory.v1",
                "config": {
                    "storage_path": self._config.storage_path,
                    "auto_snapshot": self._config.auto_snapshot,
                },
                "nodes": nodes,
                "edges": edges,
            }

    @classmethod
    def import_state(cls, payload: dict[str, Any]) -> MemoryLayer:
        """从 dict 恢复 MemoryLayer."""
        if payload.get("schema") != "dmsa.memory.v1":
            raise MemoryLayerError(
                f"unsupported schema: {payload.get('schema')!r}",
                details={"schema": payload.get("schema")},
            )
        config_data = payload.get("config", {})
        config = MemoryLayerConfig(
            storage_path=config_data.get("storage_path", ":memory:"),
            auto_snapshot=config_data.get("auto_snapshot", False),
        )
        from pmd.store.memory_store import InMemoryStore

        from dmsa.memory.layer import MemoryLayer

        # 导入是独立快照, 不重新打开 payload 中记录的原始数据库.
        layer = MemoryLayer(config=config, store=InMemoryStore())
        # 节点优先使用独立 edge 清单, 兼容旧 payload 中仅有 node.parents 的格式.
        nodes = payload.get("nodes", {})
        exported_edges = list(payload.get("edges", {}).values())
        parent_edges: dict[str, list[CreatorEdge]] = {}
        for edge_data in exported_edges:
            edge = CreatorEdge.model_validate(edge_data)
            parent_edges.setdefault(edge.to_node_id, []).append(edge)
        for node_id, node_data in nodes.items():
            if node_id not in parent_edges:
                node = PMDMemoryNode.model_validate(node_data)
                if node.parents:
                    parent_edges[node_id] = list(node.parents)
        ordered_ids: list[str] = []
        pending = set(nodes)
        while pending:
            ready = sorted(
                node_id
                for node_id in pending
                if all(edge.from_node_id not in pending for edge in parent_edges.get(node_id, []))
            )
            if not ready:
                raise MemoryLayerError("exported memory graph contains a cycle")
            ordered_ids.extend(ready)
            pending.difference_update(ready)
        for node_id in ordered_ids:
            node_data = nodes[node_id]
            node = PMDMemoryNode.model_validate(node_data)
            # SQLite reopen 的 DAG 边存于 _edges, 节点本身可能不含 parents。
            parents = parent_edges.get(node.id, list(node.parents))
            normalized = []
            for edge in parents:
                if not edge.to_node_id:
                    edge_dict = edge.model_dump()
                    edge_dict["to_node_id"] = node.id
                    edge = CreatorEdge.model_validate(edge_dict)
                normalized.append(edge)
            node_with_edges = node.model_copy(update={"parents": normalized})
            layer._dag.add_node(node_with_edges, compute_embedding=False)
        return layer
