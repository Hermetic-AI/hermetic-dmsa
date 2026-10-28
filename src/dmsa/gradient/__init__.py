"""L2 semantic gradient aggregation and offline policy evaluation."""

from __future__ import annotations

from dmsa.gradient.aggregator import SGAAggregator
from dmsa.gradient.attribution import (
    Attribution,
    attribute_feedback,
    attribute_task,
    persist_attribution,
)
from dmsa.gradient.clustering import (
    ClusteringBackend,
    FallbackSingleLinkBackend,
    HDBSCANBackend,
)
from dmsa.gradient.effectiveness import EffectivenessReport, measure_effectiveness
from dmsa.gradient.embedding import EmbedderProtocol, HashEmbedder
from dmsa.gradient.fusion import FusionClient, RuleBasedFusion, StaticFusion
from dmsa.gradient.llm import LLMFusion, OpenAIEmbedder, build_embedder
from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer, PolicyAdvice
from dmsa.gradient.policy import (
    FailureDiagnostic,
    OfflineRSIError,
    RetrievalPolicy,
    propose_policy,
)
from dmsa.gradient.replay import PolicyRegistry, gate, run_replay
from dmsa.gradient.replay_models import ReplayCase, ReplayReport
from dmsa.gradient.routing import ConditionalBranchSynthesizer, ConflictDetector
from dmsa.gradient.samples import (
    FeedbackSignal,
    GradientSample,
    make_feedback,
    make_sample,
)

__all__ = [
    "Attribution",
    "ClusteringBackend",
    "ConditionalBranchSynthesizer",
    "ConflictDetector",
    "EffectivenessReport",
    "EmbedderProtocol",
    "FailureDiagnostic",
    "FallbackSingleLinkBackend",
    "FeedbackSignal",
    "FusionClient",
    "GradientSample",
    "HDBSCANBackend",
    "HashEmbedder",
    "LLMFusion",
    "ModelAssistedPolicyProposer",
    "OfflineRSIError",
    "OpenAIEmbedder",
    "PolicyAdvice",
    "PolicyRegistry",
    "ReplayCase",
    "ReplayReport",
    "RetrievalPolicy",
    "RuleBasedFusion",
    "SGAAggregator",
    "StaticFusion",
    "attribute_feedback",
    "attribute_task",
    "build_embedder",
    "gate",
    "make_feedback",
    "make_sample",
    "measure_effectiveness",
    "persist_attribution",
    "propose_policy",
    "run_replay",
]
