"""L2 语义梯度聚合（SGA）。

7 个模块：

- :mod:`dmsa.gradient.samples` — GradientSample / FeedbackSignal 数据类
- :mod:`dmsa.gradient.embedding` — 零依赖哈希嵌入 + Protocol
- :mod:`dmsa.gradient.clustering` — HDBSCAN 接口 + fallback 单链接
- :mod:`dmsa.gradient.fusion` — StaticFusion 默认 + Protocol
- :mod:`dmsa.gradient.llm` — OpenAI 嵌入器 + LLM 融合器（可选依赖 openai）
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
from dmsa.gradient.embedding import EmbedderProtocol, HashEmbedder
from dmsa.gradient.fusion import FusionClient, RuleBasedFusion, StaticFusion
from dmsa.gradient.llm import LLMFusion, OpenAIEmbedder, build_embedder
from dmsa.gradient.routing import ConditionalBranchSynthesizer, ConflictDetector
from dmsa.gradient.samples import (
    FeedbackSignal,
    GradientSample,
    make_feedback,
    make_sample,
)

__all__ = [
    "ClusteringBackend",
    "ConditionalBranchSynthesizer",
    "ConflictDetector",
    "EmbedderProtocol",
    "FallbackSingleLinkBackend",
    "FeedbackSignal",
    "FusionClient",
    "GradientSample",
    "HDBSCANBackend",
    "HashEmbedder",
    "LLMFusion",
    "OpenAIEmbedder",
    "RuleBasedFusion",
    "SGAAggregator",
    "StaticFusion",
    "build_embedder",
    "make_feedback",
    "make_sample",
]
