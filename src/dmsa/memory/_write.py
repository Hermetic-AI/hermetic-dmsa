"""记忆层职责模块."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from pmd import (
    ContentType as PMDContentType,
)
from pmd import (
    CreatorEdge,
)
from pmd import (
    EdgeType as PMDEdgeType,
)
from pmd import (
    MemoryNode as PMDMemoryNode,
)
from pmd import (
    PromptContext as PMDPromptContext,
)
from pmd.core.enums import NodeStatus as PMDNodeStatus

from dmsa.errors import (
    MemoryLayerError,
    MissingParentError,
)
from dmsa.memory._read import MemoryRead
from dmsa.memory.prompt_context import _new_ulid
from dmsa.observability import get_logger, get_registry, start_span

_logger = get_logger("dmsa.memory.layer")
_metrics = get_registry()


class MemoryWrite(MemoryRead):
    """提供记忆层的独立职责实现."""

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
        """写入一条带 creator_prompt_id 的 memory node.

        Args:
            content: 记忆内容文本.
            prompt_context: 创建这次写入的 prompt context(PMD PromptContext).
            parents: 父节点 ULID 列表(用于构建 lineage).
            content_type: 内容类型(TEXT/EMBEDDING/...).
            requires_grad: 是否参与反向传播.
            grad_learning_rate: ``[0, 1]``,0.0 = 硬边界.
            ttl: 过期时间(秒);None = 永不过期.
            tags: 标签列表.
            importance: ``[0, 1]``,用于检索排序.

        Returns:
            持久化后的 :class:`pmd.MemoryNode`.

        Raises:
            MissingParentError: parents 中某个 ID 不存在.
        """
        with start_span("dmsa.add_memory", content_length=len(content)) as span:
            try:
                # 1. 构造 node(edges 由 add_node_atomic 根据 parents 列表自动生成)
                node = PMDMemoryNode(
                    id=_new_ulid(),  # 显式生成 ULID(PMD 不会自动生成)
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
                    # 显式保存 prompt context(PMD 不会自动 put_prompt_context)
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
        """添加一条边."""
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

    def apply_text_gradient(
        self,
        *,
        node_id: str,
        gradient_text: str,
        prompt_context: PMDPromptContext,
    ) -> PMDMemoryNode:
        """把一条文本梯度应用到记忆节点,生成 successor 节点.

        v0.1 语义:新节点 content 为原 content 追加梯度指令;
        ``parents=[node_id]`` 建立 lineage,可通过 snapshot/rollback 回滚.

        Args:
            node_id: 目标记忆节点 ULID.
            gradient_text: SGA 融合后的文本梯度.
            prompt_context: 本次应用操作的 prompt context.

        Returns:
            新生成的 successor 节点.

        Raises:
            LineageNotFoundError: 目标节点不存在.
            MemoryLayerError: 目标节点 ``requires_grad=False``(硬边界,
                声明了不参与反向传播).
        """
        with start_span("dmsa.apply_text_gradient", node_id=node_id):
            node = self.get_node(node_id)
            if not node.requires_grad or node.grad_learning_rate <= 0:
                raise MemoryLayerError(
                    f"node {node_id!r} is a hard boundary for gradient updates",
                    details={
                        "node_id": node_id,
                        "hint": "recreate the node with requires_grad=True",
                    },
                )
            merged = f"{node.content}\n\n[gradient-update] {gradient_text}"
            successor = self.add_memory(
                content=merged,
                prompt_context=prompt_context,
                parents=[node_id],
                requires_grad=node.requires_grad,
                grad_learning_rate=node.grad_learning_rate,
                tags=[*node.tags, "gradient-update"],
                importance=node.importance,
            )
            _metrics.counter("dmsa.apply_text_gradient").inc()
            _logger.info(
                "text gradient applied",
                node_id=node_id,
                successor_id=successor.id,
            )
            return successor
