"""L2 SGA 测试。"""

from __future__ import annotations

import pytest

from dmsa.errors import (
    EmbeddingFailedError,
    FusionConflictError,
    GradientAggregationError,
)
from dmsa.gradient import (
    ConflictDetector,
    ConditionalBranchSynthesizer,
    FallbackSingleLinkBackend,
    FusionClient,
    HashEmbedder,
    RuleBasedFusion,
    SGAAggregator,
    StaticFusion,
    make_sample,
)
from dmsa.gradient.clustering import HDBSCANBackend, OUTLIER_CLUSTER_ID, get_default_backend
from dmsa.gradient.embedding import (
    cosine_distance,
    cosine_similarity,
    normalize_vector,
    vector_mean,
)
from dmsa.gradient.routing import SynthesizedBranch


# ============================================================================
# HashEmbedder
# ============================================================================


def test_hash_embedder_deterministic() -> None:
    """HashEmbedder 确定性：相同输入相同输出。"""
    embedder = HashEmbedder(dim=64)
    v1 = embedder.embed("hello")
    v2 = embedder.embed("hello")
    assert v1 == v2
    assert len(v1) == 64


def test_hash_embedder_different_for_different_text() -> None:
    """HashEmbedder 对不同输入产生不同输出。"""
    embedder = HashEmbedder(dim=64)
    v1 = embedder.embed("hello")
    v2 = embedder.embed("world")
    assert v1 != v2


def test_hash_embedder_dim_validation() -> None:
    """dim <= 0 抛错。"""
    with pytest.raises(ValueError):
        HashEmbedder(dim=0)


def test_hash_embedder_batch() -> None:
    """embed_batch 批量处理。"""
    embedder = HashEmbedder(dim=32)
    results = embedder.embed_batch(["a", "b", "c"])
    assert len(results) == 3
    assert all(len(v) == 32 for v in results)


def test_hash_embedder_rejects_non_string() -> None:
    """非 str 输入抛错。"""
    embedder = HashEmbedder()
    with pytest.raises(TypeError):
        embedder.embed(123)  # type: ignore[arg-type]


# ============================================================================
# 余弦工具
# ============================================================================


def test_cosine_similarity_identical() -> None:
    """相同向量余弦相似度 = 1.0。"""
    v = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal() -> None:
    """正交向量余弦相似度 = 0.0。"""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_distance_invariant() -> None:
    """cosine_distance = 1 - cosine_similarity。"""
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    sim = cosine_similarity(a, b)
    dist = cosine_distance(a, b)
    assert abs(sim + dist - 1.0) < 1e-6


def test_cosine_dim_mismatch_raises() -> None:
    """维度不匹配抛错。"""
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def test_normalize_vector() -> None:
    """归一化后模长 = 1。"""
    import math

    v = [3.0, 4.0]
    n = normalize_vector(v)
    assert abs(math.sqrt(n[0] ** 2 + n[1] ** 2) - 1.0) < 1e-6


def test_vector_mean() -> None:
    """向量均值正确。"""
    result = vector_mean([[1.0, 0.0], [3.0, 0.0]])
    assert result == [2.0, 0.0]


def test_vector_mean_empty_raises() -> None:
    """空序列抛错。"""
    with pytest.raises(ValueError):
        vector_mean([])


# ============================================================================
# FallbackSingleLinkBackend
# ============================================================================


def _make_sample_with_embedding(text: str, dim: int = 32) -> object:
    """造一个带 embedding 的 GradientSample。"""
    from dmsa.types import GradientSample
    from dmsa.gradient.embedding import HashEmbedder

    embedder = HashEmbedder(dim=dim)
    vec = embedder.embed(text)
    return GradientSample(
        sample_id=text[:8],
        feedback_text=text,
        source="USER",
        severity=1.0,
        embedding=tuple(vec),
    )


def test_fallback_backend_groups_similar_samples() -> None:
    """相同文本（嵌入完全相同）应聚到同一簇。"""
    embedder = HashEmbedder(dim=32)
    # 两个完全相同的样本 + 一个 outlier
    samples = [
        _make_sample_with_embedding("apple banana"),
        _make_sample_with_embedding("apple banana"),
        _make_sample_with_embedding("completely different quantum physics"),
    ]
    backend = FallbackSingleLinkBackend(min_cluster_size=2, distance_threshold=0.1)
    clusters = backend.cluster(samples)
    # 2 个 cluster：1 个正常 + 1 个 outlier（包含 quantum physics）
    non_outlier = [c for c in clusters if not c.is_outlier]
    outlier_clusters = [c for c in clusters if c.is_outlier]
    assert len(non_outlier) == 1
    assert len(outlier_clusters) == 1
    assert len(non_outlier[0].support_set) == 2
    assert len(outlier_clusters[0].support_set) == 1


def test_fallback_backend_rejects_unembedded() -> None:
    """未嵌入样本抛 :class:`ClusteringFailedError`。"""
    from dmsa.types import GradientSample

    samples = [
        GradientSample(
            sample_id="x", feedback_text="a", source="U", severity=1.0, embedding=None
        )
    ]
    backend = FallbackSingleLinkBackend()
    with pytest.raises(Exception):  # ClusteringFailedError
        backend.cluster(samples)


def test_fallback_backend_empty_input() -> None:
    """空输入返回空列表。"""
    backend = FallbackSingleLinkBackend()
    assert backend.cluster([]) == []


def test_fallback_backend_centroid_computed() -> None:
    """质心 = 簇内向量均值。"""
    embedder = HashEmbedder(dim=8)
    samples = [
        _make_sample_with_embedding("a", dim=8),
        _make_sample_with_embedding("a", dim=8),  # 完全相同
    ]
    backend = FallbackSingleLinkBackend(min_cluster_size=2, distance_threshold=0.1)
    clusters = backend.cluster(samples)
    assert clusters[0].centroid is not None
    # 质心应等于单个样本的 embedding
    assert clusters[0].centroid == samples[0].embedding


def test_fallback_backend_confidence_is_1_when_perfect() -> None:
    """完全相同的样本簇 confidence ≈ 1.0。"""
    samples = [
        _make_sample_with_embedding("x"),
        _make_sample_with_embedding("x"),
    ]
    backend = FallbackSingleLinkBackend(min_cluster_size=2, distance_threshold=0.1)
    clusters = backend.cluster(samples)
    assert clusters[0].confidence == pytest.approx(1.0)


def test_get_default_backend_returns_fallback() -> None:
    """默认 backend 是 fallback。"""
    backend = get_default_backend()
    assert isinstance(backend, FallbackSingleLinkBackend)


# ============================================================================
# StaticFusion
# ============================================================================


def test_static_fusion_basic() -> None:
    """StaticFusion 拼接所有 feedback_text。"""
    from dmsa.types import GradientSample

    samples = [
        GradientSample(
            sample_id="a", feedback_text="first feedback", source="U", severity=0.5
        ),
        GradientSample(
            sample_id="b", feedback_text="second feedback", source="U", severity=0.7
        ),
    ]
    fusion = StaticFusion()
    fused = fusion.fuse(cluster_id=0, samples=samples)
    assert "first feedback" in fused.fused_text
    assert "second feedback" in fused.fused_text
    assert fused.support_size == 2
    assert fused.has_conflict is False


def test_static_fusion_empty_raises() -> None:
    """空样本列表抛 :class:`FusionConflictError`。"""
    fusion = StaticFusion()
    with pytest.raises(FusionConflictError):
        fusion.fuse(cluster_id=0, samples=[])


# ============================================================================
# RuleBasedFusion
# ============================================================================


def test_rule_based_detects_conflict_with_negation_and_directive() -> None:
    """簇内既有"不要"又有"必须"时标 has_conflict=True。"""
    from dmsa.types import GradientSample

    samples = [
        GradientSample(
            sample_id="a", feedback_text="不要使用 markdown", source="U", severity=1.0
        ),
        GradientSample(
            sample_id="b", feedback_text="应该 always use markdown", source="U", severity=1.0
        ),
    ]
    fusion = RuleBasedFusion()
    fused = fusion.fuse(cluster_id=0, samples=samples)
    assert fused.has_conflict is True


def test_rule_based_no_conflict_when_only_directive() -> None:
    """仅有 directive 不标冲突。"""
    from dmsa.types import GradientSample

    samples = [
        GradientSample(
            sample_id="a", feedback_text="应该详细", source="U", severity=1.0
        ),
        GradientSample(
            sample_id="b", feedback_text="应该简洁", source="U", severity=1.0
        ),
    ]
    fusion = RuleBasedFusion()
    fused = fusion.fuse(cluster_id=0, samples=samples)
    # "应该简洁" 触发 directive 但没 negation → 没冲突
    assert fused.has_conflict is False


# ============================================================================
# ConflictDetector
# ============================================================================


def test_conflict_detector_finds_distant_clusters() -> None:
    """距离 > 阈值的簇对被标为冲突。"""
    from dmsa.types import GradientSample

    # 两个完全不同的样本
    sa = GradientSample(
        sample_id="a",
        feedback_text="apple",
        source="U",
        severity=1.0,
        embedding=tuple([1.0] + [0.0] * 31),
    )
    sb = GradientSample(
        sample_id="b",
        feedback_text="banana",
        source="U",
        severity=1.0,
        embedding=tuple([-1.0] + [0.0] * 31),
    )
    fused_a = _make_fused(0, "apple text", sa)
    fused_b = _make_fused(1, "banana text", sb)
    detector = ConflictDetector(threshold=0.5)
    conflicts = detector.detect([fused_a, fused_b], {0: [sa], 1: [sb]})
    assert len(conflicts) == 1
    assert conflicts[0][0] == 0
    assert conflicts[0][1] == 1


def test_conflict_detector_threshold_validation() -> None:
    """threshold 必须在 [0, 1]。"""
    with pytest.raises(ValueError):
        ConflictDetector(threshold=1.5)


def _make_fused(cluster_id: int, text: str, sample: object) -> object:
    """构造 FusedGradient。"""
    from dmsa.types import FusedGradient

    return FusedGradient(
        cluster_id=cluster_id,
        fused_text=text,
        support_size=1,
        confidence=0.5,
        has_conflict=False,
    )


# ============================================================================
# ConditionalBranchSynthesizer
# ============================================================================


def test_branch_synthesizer_creates_branch() -> None:
    """synthesize 返回 SynthesizedBranch。"""
    from dmsa.types import FusedGradient

    fa = FusedGradient(
        cluster_id=0, fused_text="use detailed verbose text", support_size=1, confidence=0.5
    )
    fb = FusedGradient(
        cluster_id=1, fused_text="use short concise text", support_size=1, confidence=0.5
    )
    syn = ConditionalBranchSynthesizer()
    branch = syn.synthesize(fa, fb)
    assert isinstance(branch, SynthesizedBranch)
    assert "verbose" in branch.then_text or "concise" in branch.else_text
    # 条件应该提到 verbosity 关键词
    assert branch.condition != "context"  # 检测到了 verbosity


def test_branch_synthesizer_synthesize_all() -> None:
    """synthesize_all 为每个冲突簇生成条件分支。"""
    from dmsa.types import FusedGradient

    fa = FusedGradient(
        cluster_id=0, fused_text="use detailed text", support_size=1, confidence=0.5
    )
    fb = FusedGradient(
        cluster_id=1, fused_text="use concise text", support_size=1, confidence=0.5
    )
    syn = ConditionalBranchSynthesizer()
    branches = syn.synthesize_all(
        conflicts=[(0, 1, 0.8)],
        fused_by_id={0: fa, 1: fb},
    )
    assert 0 in branches
    assert 1 in branches
    assert len(branches[0]) == 1
    assert len(branches[1]) == 1


# ============================================================================
# SGAAggregator 端到端
# ============================================================================


def test_aggregator_empty_samples() -> None:
    """空样本返回空结果。"""
    agg = SGAAggregator()
    result = agg.aggregate([])
    assert result.clusters == ()
    assert result.fused_gradients == ()


def test_aggregator_too_many_samples_raises() -> None:
    """超过 max_samples 抛错。"""
    from dmsa.types import GradientSample

    samples = [
        GradientSample(
            sample_id=str(i), feedback_text="x", source="U", severity=1.0
        )
        for i in range(11)
    ]
    agg = SGAAggregator(max_samples=10)
    with pytest.raises(GradientAggregationError):
        agg.aggregate(samples)


def test_aggregator_max_samples_validation() -> None:
    """max_samples <= 0 抛错。"""
    with pytest.raises(ValueError):
        SGAAggregator(max_samples=0)


def test_aggregator_basic_flow() -> None:
    """完整 SGA 流程：embed -> cluster -> fuse。"""
    samples = [
        make_sample(feedback_text="use markdown headers"),
        make_sample(feedback_text="use markdown headers"),
        make_sample(feedback_text="no markdown please"),
        make_sample(feedback_text="no markdown please"),
    ]
    agg = SGAAggregator()
    result = agg.aggregate(samples, feedback_id="fb1")
    # 至少 1 个 cluster + 可能 1 个 outlier
    assert len(result.clusters) >= 1
    assert result.feedback_id == "fb1"


def test_aggregator_embeds_automatically() -> None:
    """make_sample 默认无 embedding；aggregator 自动嵌入。"""
    samples = [make_sample(feedback_text="hello world")]
    agg = SGAAggregator()
    result = agg.aggregate(samples)
    # 至少产生 1 个 fused（outlier 也算）
    assert len(result.fused_gradients) >= 1


def test_aggregator_handles_identical_samples() -> None:
    """全相同样本聚到 1 簇。"""
    samples = [make_sample(feedback_text="same text") for _ in range(5)]
    agg = SGAAggregator()
    result = agg.aggregate(samples)
    non_outlier = [f for f in result.fused_gradients if not f.cluster_id == OUTLIER_CLUSTER_ID]
    assert len(non_outlier) == 1
    assert non_outlier[0].support_size == 5


def test_aggregator_conflicting_samples_generate_branches() -> None:
    """冲突样本触发条件分支生成。"""
    samples = [
        make_sample(feedback_text="use verbose detailed text", source="USER", severity=1.0),
        make_sample(feedback_text="use verbose detailed text", source="USER", severity=1.0),
        make_sample(feedback_text="use short concise text", source="USER", severity=1.0),
        make_sample(feedback_text="use short concise text", source="USER", severity=1.0),
    ]
    agg = SGAAggregator(
        fusion=RuleBasedFusion(),  # 显式用 rule-based 检测冲突
    )
    result = agg.aggregate(samples)
    # 至少一个 fused 有 has_conflict=True 或 branch_conditions 非空
    any_conflict = any(
        f.has_conflict or len(f.branch_conditions) > 0 for f in result.fused_gradients
    )
    assert any_conflict
