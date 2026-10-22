"""L2 semantic gradient aggregation and offline policy evaluation."""

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
from dmsa.gradient.policy import (
    FailureDiagnostic,
    OfflineRSIError,
    RetrievalPolicy,
    propose_policy,
)
from dmsa.gradient.replay import PolicyRegistry, ReplayCase, ReplayReport, gate, run_replay
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
    "FailureDiagnostic",
    "FallbackSingleLinkBackend",
    "FeedbackSignal",
    "FusionClient",
    "GradientSample",
    "HDBSCANBackend",
    "HashEmbedder",
    "LLMFusion",
    "OfflineRSIError",
    "OpenAIEmbedder",
    "PolicyRegistry",
    "ReplayCase",
    "ReplayReport",
    "RetrievalPolicy",
    "RuleBasedFusion",
    "SGAAggregator",
    "StaticFusion",
    "build_embedder",
    "gate",
    "make_feedback",
    "make_sample",
    "propose_policy",
    "run_replay",
]
