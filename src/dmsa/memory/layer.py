"""MemoryLayer — L1 主入口（封装 pmd.ProvenanceDAG）。

设计原则：

1. **薄封装**：不重新发明 PMD 的能力，只在 PMD 之上加：
   - 类型安全的 dmsa 公共 API
   - dmsa 错误体系（PD4）
   - 可观测性 hooks（PD6：每个关键操作记 metric + log + trace）
   - 配置驱动的 embedder / storage 注入（PD7）

2. **可降级**：不强制使用 PMD 的 SQLite store；测试时可传 None 用纯内存模式
   （调用方自行管理 store 生命周期）。

3. **可观测**：所有公共方法用 :func:`dmsa.observability.start_span` 包裹。

依赖规则：只 import pmd + dmsa.types + dmsa.errors + dmsa.observability。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Mapping

from pmd import (
    ContentType as PMDContentType,
    CreatorEdge,
    EdgeType as PMDEdgeType,
    MemoryNode as PMDMemoryNode,
    PropagationStrategy as PMDPropagationStrategy,
    PromptContext as PMDPromptContext,
    ProvenanceDAG,
    VersionSnapshot,
)
from pmd.core.enums import FeedbackSource as PMDFeedbackSource
from pmd.core.enums import NodeStatus as PMDNodeStatus

from dmsa.errors import (
    LineageNotFoundError,
    MemoryLayerError,
    MissingParentError,
)
from dmsa.memory.lineage_query import (
    LineageDirection,
    LineageQuery,
    to_pmd_edge_types,
    validate_query,
)
from dmsa.memory.prompt_context import _new_ulid, create_prompt_context
from dmsa.observability import get_logger, get_registry, start_span
from dmsa.types import MemoryLayerConfig

__all__ = ["MemoryLayer"]


_logger = get_logger("dmsa.memory.layer")
_metrics = get_registry()


class MemoryLayer:
    """记忆层主入口。薄封装 :class:`pmd.ProvenanceDAG`。

    Args:
        config: 运行配置（storage / embedder 等）。
        embedder: 可选嵌入器（任何实现 ``embed(str) -> list[float]`` 的对象）。
        store: 可选 PMD 存储后端；不传则用 :class:`pmd.InMemoryStore`。

    Example:
        >>> from dmsa import DMSAConfig
        >>> from dmsa.memory import MemoryLayer
        >>> layer = MemoryLayer(config=DMSAConfig())
        >>> node = layer.add_memory(content="hello", prompt_context=ctx)
    """

    def __init__(
        self,
        *,
        config: MemoryLayerConfig | None = None,
        embedder: Any | None = None,
        store: Any | None = None,
    ) -> None:
        self._config = config or MemoryLayerConfig()
        self._embedder = embedder
        self._lock = threading.RLock()

        # 存储后端：默认走 PMD 内存 store（PMD skeleton 自带）
        if store is None:
            from pmd.store.memory_store import InMemoryStore

            store = InMemoryStore()

        self._store = store
        # PMD 的 ProvenanceDAG
        self._dag: ProvenanceDAG = ProvenanceDAG(store=store, embedder=embedder)

    # ------------------------------------------------------------------
    # 公共 API：写入
    # ------------------------------------------------------------------

    def add_memory(
        self,
        *,
        content: str,
        prompt_context: PMDPromptContext,
        parents: Iterable[str] = (),
        content_type: PMDContentType = PMDContentType.TEXT,
        requires_grad: bool = False,
        grad_learning_rate: float = 0.0,
        ttl: float | None = None,
        tags: Iterable[str] = (),
        importance: float = 0.5,
    ) -> PMDMemoryNode:
        """写入一条带 creator_prompt_id 的 memory node。

        Args:
            content: 记忆内容文本。
            prompt_context: 创建这次写入的 prompt context（PMD PromptContext）。
            parents: 父节点 ULID 列表（用于构建 lineage）。
            content_type: 内容类型（TEXT/EMBEDDING/...）。
            requires_grad: 是否参与反向传播。
            grad_learning_rate: ``[0, 1]``，0.0 = 硬边界。
            ttl: 过期时间（秒）；None = 永不过期。
            tags: 标签列表。
            importance: ``[0, 1]``，用于检索排序。

        Returns:
            持久化后的 :class:`pmd.MemoryNode`。

        Raises:
            MissingParentError: parents 中某个 ID 不存在。
        """
        with start_span("dmsa.add_memory", content_length=len(content)) as span:
            try:
                # 1. 构造 node（edges 由 add_node_atomic 根据 parents 列表自动生成）
                node = PMDMemoryNode(
                    id=_new_ulid(),  # 显式生成 ULID（PMD 不会自动生成）
                    version=1,
                    content=content,
                    content_type=content_type,
                    embedding=None,
                    embedding_model=self._embedder.name if self._embedder else None,
                    structured_payload=None,
                    creator_prompt_id=prompt_context.prompt_id,
                    creator_agent_id=prompt_context.agent_id,
                    creation_context={
                        "system_prompt": prompt_context.system_prompt[:200],
                        "user_prompt": prompt_context.user_prompt[:200],
                        "model_name": prompt_context.model_name,
                        "temperature": prompt_context.temperature,
                    },
                    parents=[],  # PMD add_node_atomic 会填充
                    children=[],
                    sibling_ids=[],
                    requires_grad=requires_grad,
                    grad_learning_rate=grad_learning_rate,
                    created_at=time.time(),
                    updated_at=time.time(),
                    ttl=ttl,
                    last_accessed_at=time.time(),
                    access_count=0,
                    status=PMDNodeStatus.ACTIVE,
                    checkpoint_id=None,
                    lineage_depth=0,
                    importance=importance,
                    tags=list(tags),
                    accumulated_feedback="",
                    feedback_count=0,
                    last_feedback_at=None,
                )

                with self._lock:
                    # 显式保存 prompt context（PMD 不会自动 put_prompt_context）
                    if hasattr(self._store, "put_prompt_context"):
                        self._store.put_prompt_context(prompt_context)
                    persisted = self._dag.add_node_atomic(
                        node,
                        parents=list(parents),
                        edge_type=PMDEdgeType.DERIVED_FROM,
                    )

                _metrics.counter("dmsa.add_memory").inc()
                span.set_attribute("node_id", persisted.id)
                _logger.info(
                    "memory added",
                    node_id=persisted.id,
                    content_length=len(content),
                    requires_grad=requires_grad,
                )
                return persisted
            except MissingParentError:
                raise
            except Exception as exc:
                _logger.error("add_memory failed", error=str(exc))
                if "missing parent" in str(exc).lower() or "not found" in str(exc).lower():
                    raise MissingParentError(
                        str(exc),
                        details={"original": type(exc).__name__},
                    ) from exc
                raise MemoryLayerError(
                    f"add_memory failed: {exc}",
                    details={"error": str(exc)},
                ) from exc

    def add_edge(
        self,
        *,
        from_id: str,
        to_id: str,
        edge_type: PMDEdgeType = PMDEdgeType.DERIVED_FROM,
        weight: float = 1.0,
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> CreatorEdge:
        """添加一条边。"""
        with start_span("dmsa.add_edge", from_id=from_id, to_id=to_id):
            edge = CreatorEdge(
                from_node_id=from_id,
                to_node_id=to_id,
                edge_type=edge_type,
                weight=weight,
                confidence=confidence,
                metadata=dict(metadata) if metadata else {},
                created_at=time.time(),
                feedback_text="",
                feedback_token_cost=0,
            )
            with self._lock:
                self._dag.add_edge(edge)
            _metrics.counter("dmsa.add_edge").inc()
            return edge

    # ------------------------------------------------------------------
    # 公共 API：读取
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> PMDMemoryNode:
        """获取节点；不存在抛 :class:`LineageNotFoundError`。"""
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
        """获取祖先节点（沿 parents 上行）。"""
        with self._lock:
            try:
                et = list(edge_types) if edge_types else None
                if depth is not None:
                    return self._dag.get_ancestors(node_id, depth=depth, edge_types=et)
                return self._dag.get_ancestors(node_id, edge_types=et)
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
        """获取后代节点（沿 children 下行）。"""
        with self._lock:
            try:
                et = list(edge_types) if edge_types else None
                if depth is not None:
                    return self._dag.get_descendants(
                        node_id, depth=depth, edge_types=et
                    )
                return self._dag.get_descendants(node_id, edge_types=et)
            except LineageNotFoundError:
                raise
            except Exception as exc:
                raise LineageNotFoundError(
                    f"get_descendants({node_id!r}) failed: {exc}",
                    details={"node_id": node_id, "depth": depth},
                ) from exc

    def query_lineage(self, query: LineageQuery) -> list[PMDMemoryNode]:
        """用 :class:`LineageQuery` 描述符查 lineage。"""
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
        """O(1) creator 查找；返回 None 表示没记录。"""
        with self._lock:
            return self._dag.get_creator(node_id)

    # ------------------------------------------------------------------
    # 公共 API：版本
    # ------------------------------------------------------------------

    def snapshot(
        self,
        *,
        description: str = "",
        tag: str | None = None,
    ) -> VersionSnapshot:
        """打点版本。"""
        with self._lock:
            return self._dag.snapshot(description=description, tag=tag)

    def rollback(self, version_id: str) -> None:
        """回滚到指定版本。"""
        with self._lock:
            self._dag.rollback(version_id)

    def list_versions(self) -> list[VersionSnapshot]:
        """列出所有版本快照。"""
        with self._lock:
            return self._dag.list_versions()

    # ------------------------------------------------------------------
    # 公共 API：检索
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        k: int = 10,
    ) -> list[PMDMemoryNode]:
        """纯向量检索（需要 embedder）。"""
        with self._lock:
            if self._embedder is None:
                raise MemoryLayerError(
                    "retrieve() requires an embedder to be attached",
                    details={"hint": "pass embedder= when constructing MemoryLayer"},
                )
            return self._dag.lineage_aware_search(
                query,
                k=k,
                depth=0,  # 纯向量，不展开 lineage
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
        """lineage-aware 检索（需要 embedder）。"""
        with self._lock:
            if self._embedder is None:
                raise MemoryLayerError(
                    "lineage_aware_search requires an embedder",
                    details={"hint": "pass embedder= when constructing MemoryLayer"},
                )
            return self._dag.lineage_aware_search(
                query,
                k=k,
                depth=depth,
                alpha=alpha,
                beta=beta,
                gamma=gamma,
            )

    # ------------------------------------------------------------------
    # 内部：序列化（被 serialization 模块调用，避免循环 import）
    # ------------------------------------------------------------------

    def export_state(self) -> dict[str, Any]:
        """把当前 MemoryLayer 状态导出为 dict。"""
        with self._lock:
            nodes = {nid: n.model_dump(mode="json") for nid, n in self._dag._nodes.items()}
            edges = {
                f"{e.from_node_id}|{e.to_node_id}|{e.edge_type.value}": e.model_dump(
                    mode="json"
                )
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
    def import_state(cls, payload: dict[str, Any]) -> "MemoryLayer":
        """从 dict 恢复 MemoryLayer。"""
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
        layer = cls(config=config)
        # 重建 nodes（不重建 edges — node.parents 字段已含 edges，PMD 会自动添加）
        for node_data in payload.get("nodes", {}).values():
            node = PMDMemoryNode.model_validate(node_data)
            # 把 to_node_id 填上（dump 时是空）
            parents = node.parents
            for edge in parents:
                if not edge.to_node_id:
                    edge_dict = edge.model_dump()
                    edge_dict["to_node_id"] = node.id
                    edge = CreatorEdge.model_validate(edge_dict)
            node_with_edges = node.model_copy(update={"parents": parents})
            layer._dag.add_node(node_with_edges, compute_embedding=False)
        return layer

    # ------------------------------------------------------------------
    # 内部：dag / store 访问（供 L2/L3 调用；不暴露给用户）
    # ------------------------------------------------------------------

    @property
    def dag(self) -> ProvenanceDAG:
        """暴露给 L2/L3 模块调用（不应被最终用户直接使用）。"""
        return self._dag

    @property
    def store(self) -> Any:
        """存储后端。"""
        return self._store

    @property
    def embedder(self) -> Any:
        """嵌入器。"""
        return self._embedder

    def __len__(self) -> int:
        with self._lock:
            return len(self._dag)

    def __contains__(self, node_id: object) -> bool:
        with self._lock:
            return node_id in self._dag
