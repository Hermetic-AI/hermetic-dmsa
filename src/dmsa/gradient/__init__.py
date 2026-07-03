"""L2 语义梯度聚合（SGA）。

6 个模块：

- :mod:`dmsa.gradient.samples` — GradientSample / FeedbackSignal 数据类
- :mod:`dmsa.gradient.embedding` — 零依赖哈希嵌入 + Protocol
- :mod:`dmsa.gradient.clustering` — HDBSCAN 接口 + fallback 单链接
- :mod:`dmsa.gradient.fusion` — StaticFusion 默认 + Protocol
- :mod:`dmsa.gradient.routing` — 冲突检测 + 条件分支
- :mod:`dmsa.gradient.aggregator` — :class:`SGAAggregator` 编排

依赖规则：本层只 import L1 + pmd，不 import L3/L4。
"""

from __future__ import annotations

from dmsa.gradient.aggregator import SGAAggregator
from dmsa.gradient.clustering import (
    ClusteringBackend,
    FallbackSingleLinkBackend,
    HDBSCANBackend,
)
from dmsa.gradient.embedding import HashEmbedder, EmbedderProtocol
from dmsa.gradient.fusion import FusionClient, RuleBasedFusion, StaticFusion
from dmsa.gradient.routing import ConflictDetector, ConditionalBranchSynthesizer
from dmsa.gradient.samples import (
    FeedbackSignal,
    GradientSample,
    make_feedback,
    make_sample,
)

__all__ = [
    "SGAAggregator",
    "ClusteringBackend",
    "FallbackSingleLinkBackend",
    "HDBSCANBackend",
    "HashEmbedder",
    "EmbedderProtocol",
    "FusionClient",
    "StaticFusion",
    "RuleBasedFusion",
    "ConflictDetector",
    "ConditionalBranchSynthesizer",
    "GradientSample",
    "FeedbackSignal",
    "make_sample",
    "make_feedback",
]
