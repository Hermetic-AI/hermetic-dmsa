"""SGA Aggregator — 编排 embed + cluster + fuse + route 完整流程。

主入口 :class:`SGAAggregator`。典型用法：

    >>> aggregator = SGAAggregator(embedder=HashEmbedder())
    >>> samples = [make_sample(feedback_text="...") for _ in range(10)]
    >>> result = aggregator.aggregate(samples)
    >>> for fused in result.fused_gradients:
    ...     apply_to_prompt(fused.fused_text)
"""

from __future__ import annotations

from typing import Iterable

from dmsa.errors import EmbeddingFailedError, GradientAggregationError
from dmsa.gradient.clustering import (
    ClusteringBackend,
    FallbackSingleLinkBackend,
    OUTLIER_CLUSTER_ID,
)
from dmsa.gradient.embedding import EmbedderProtocol, HashEmbedder
from dmsa.gradient.fusion import FusionClient, StaticFusion
from dmsa.gradient.routing import (
    ConflictDetector,
    ConditionalBranchSynthesizer,
)
from dmsa.observability import get_logger, get_registry, start_span
from dmsa.types import (
    AGSAggregationResult,
    FusedGradient,
    GradientSample,
)

__all__ = ["SGAAggregator"]


_logger = get_logger("dmsa.gradient.aggregator")
_metrics = get_registry()


class SGAAggregator:
    """SGA 编排器。

    Args:
        embedder: 嵌入器实例（默认 :class:`HashEmbedder` 零依赖）。
        clustering: 聚类后端（默认 :class:`FallbackSingleLinkBackend`）。
        fusion: 融合客户端（默认 :class:`StaticFusion`）。
        conflict_detector: 冲突检测器。
        branch_synthesizer: 条件分支生成器。
        max_samples: 单次聚合最大样本数（防 DoS）。
    """

    def __init__(
        self,
        *,
        embedder: EmbedderProtocol | None = None,
        clustering: ClusteringBackend | None = None,
        fusion: FusionClient | None = None,
        conflict_detector: ConflictDetector | None = None,
        branch_synthesizer: ConditionalBranchSynthesizer | None = None,
        max_samples: int = 10_000,
    ) -> None:
        if max_samples <= 0:
            raise ValueError(f"max_samples must be positive, got {max_samples}")
        self._embedder: EmbedderProtocol = embedder or HashEmbedder()
        self._clustering: ClusteringBackend = clustering or FallbackSingleLinkBackend()
        self._fusion: FusionClient = fusion or StaticFusion()
        self._conflict_detector = conflict_detector or ConflictDetector()
        self._branch_synthesizer = branch_synthesizer or ConditionalBranchSynthesizer()
        self._max_samples = max_samples

    def aggregate(
        self,
        samples: list[GradientSample],
        *,
        feedback_id: str = "",
    ) -> AGSAggregationResult:
        """执行完整 SGA 流程。

        步骤：
        1. 嵌入未嵌入的样本
        2. 聚类
        3. 逐簇融合
        4. 冲突检测 + 条件分支生成
        5. 标记 has_conflict + branch_conditions
        6. 返回 :class:`AGSAggregationResult`
        """
        with start_span("dmsa.aggregate", sample_count=len(samples)) as span:
            if len(samples) > self._max_samples:
                raise GradientAggregationError(
                    f"too many samples: {len(samples)} > max {self._max_samples}",
                    details={"sample_count": len(samples), "max_samples": self._max_samples},
                )
            if not samples:
                return AGSAggregationResult(
                    feedback_id=feedback_id,
                    clusters=(),
                    fused_gradients=(),
                    applied_to=(),
                )

            # 1. embed
            embedded = list(self._embed_samples(samples))

            # 2. cluster
            clusters = self._clustering.cluster(embedded)
            span.set_attribute("cluster_count", len(clusters))

            # 3. fuse
            fused_list: list[FusedGradient] = []
            for cluster in clusters:
                if cluster.is_outlier:
                    # outlier 簇也生成 fused（置信度低），方便追踪
                    fused = self._fusion.fuse(cluster.cluster_id, list(cluster.support_set))
                    fused = FusedGradient(
                        cluster_id=fused.cluster_id,
                        fused_text=fused.fused_text,
                        support_size=fused.support_size,
                        confidence=0.0,
                        has_conflict=False,
                        branch_conditions=(),
                    )
                else:
                    fused = self._fusion.fuse(cluster.cluster_id, list(cluster.support_set))
                fused_list.append(fused)

            # 4-5. conflict detection + branch synthesis
            samples_by_cluster: dict[int, list[GradientSample]] = {
                c.cluster_id: list(c.support_set) for c in clusters
            }
            conflicts = self._conflict_detector.detect(fused_list, samples_by_cluster)
            fused_by_id = {f.cluster_id: f for f in fused_list}
            branches_by_cluster = self._branch_synthesizer.synthesize_all(
                conflicts, fused_by_id
            )
            # annotate fused with branches / has_conflict
            annotated: list[FusedGradient] = []
            for f in fused_list:
                bs = branches_by_cluster.get(f.cluster_id, [])
                annotated.append(
                    FusedGradient(
                        cluster_id=f.cluster_id,
                        fused_text=f.fused_text,
                        support_size=f.support_size,
                        confidence=f.confidence,
                        has_conflict=bool(bs) or f.has_conflict,
                        branch_conditions=tuple(b.to_line() for b in bs),
                    )
                )

            _metrics.counter("dmsa.aggregate.runs").inc()
            _metrics.histogram("dmsa.aggregate.samples").observe(len(samples))
            _metrics.histogram("dmsa.aggregate.clusters").observe(len(clusters))
            _logger.info(
                "aggregation complete",
                sample_count=len(samples),
                cluster_count=len(clusters),
                conflict_count=len(conflicts),
            )
            return AGSAggregationResult(
                feedback_id=feedback_id,
                clusters=tuple(clusters),
                fused_gradients=tuple(annotated),
                applied_to=(),
            )

    def _embed_samples(
        self, samples: list[GradientSample]
    ) -> Iterable[GradientSample]:
        """嵌入未嵌入的样本。"""
        for s in samples:
            if s.embedding is not None:
                yield s
                continue
            try:
                vec = self._embedder.embed(s.feedback_text)
            except Exception as exc:
                raise EmbeddingFailedError(
                    f"failed to embed sample {s.sample_id}: {exc}",
                    details={"sample_id": s.sample_id, "error": str(exc)},
                ) from exc
            yield GradientSample(
                sample_id=s.sample_id,
                feedback_text=s.feedback_text,
                source=s.source,
                severity=s.severity,
                embedding=tuple(vec),
                target_node_id=s.target_node_id,
                created_at=s.created_at,
                metadata=dict(s.metadata),
            )


# Re-export for callers
_ = OUTLIER_CLUSTER_ID
