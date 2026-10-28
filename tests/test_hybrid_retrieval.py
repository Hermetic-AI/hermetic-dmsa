"""稠密混合检索与有界重排的行为测试."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import parse_request
from dmsa.memory import CodingMemory, HybridItem, HybridPolicy, SearchPolicy, SourceMessage
from dmsa.memory.coding.hybrid import RRF_UNIT, fuse, rerank
from dmsa.memory.coding.models import CodingMemoryValidationError
from dmsa.memory.coding.recall import ChannelRecall, merge_channels

# 证据正文: 第一条是真正相关的根因, 第二条共享定位信号, 第三条无关.
ROOT_CAUSE = "根因: 缓存失效没有递增 revision。修复: 提交后递增 revision。"
RELATED = "另一个调用点也要经过 flush_buffer, 见 src/cache.py, 注意空指针。"
NOISE = "顺手把日志格式统一成一行输出, 不影响逻辑。"


class _Embedder:
    """测试用 deterministic dense backend."""

    fingerprint = "unit-v1"
    dimension = 2

    def embed_batch(self, texts):
        """返回有效的固定向量."""
        return [[1.0, 0.5] for _ in texts]


class _DiscriminativeEmbedder:
    """按正文内容给出可区分向量的测试嵌入器."""

    fingerprint = "unit-v2"
    dimension = 2

    def embed_batch(self, texts):
        """含缓存语义的文本与查询同向, 其余文本正交."""
        return [[1.0, 0.0] if "缓存" in text else [0.0, 1.0] for text in texts]


def _memory(tmp_path: Path, embedder: Any = None) -> CodingMemory:
    """构造含三条证据的 coding memory."""
    memory = CodingMemory(
        tmp_path / "hybrid.sqlite",
        embedder=embedder,
        policy=SearchPolicy(neighbor_window=0),
    )
    memory.add("r1", "u", "s1", [SourceMessage("assistant", ROOT_CAUSE + "改动在 src/cache.py。")])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", RELATED)])
    memory.add("r3", "u", "s3", [SourceMessage("assistant", NOISE)])
    return memory


def test_lexical_fusion_matches_legacy_rrf() -> None:
    """词面通道的融合分与既有 RRF 完全一致, 混合分与检索分同量纲."""
    channels = {
        "exact": ChannelRecall("exact", 1.0, ("a", "b")),
        "sparse": ChannelRecall("sparse", 1.0, ("b", "c")),
    }

    fused, _ = fuse(channels, {}, HybridPolicy())

    assert fused == merge_channels(channels)


def test_dense_channel_scores_by_similarity_not_rank() -> None:
    """稠密通道按相似度给分, 语义更近的候选拿到的分明显更高."""
    channels = {"sparse": ChannelRecall("sparse", 1.0, ("a",))}

    fused, items = fuse(channels, {"a": 0.95, "b": 0.55}, HybridPolicy())

    assert fused["a"] - fused["b"] > 0.2 * RRF_UNIT
    assert items["a"].dense_similarity == 0.95
    assert dict(items["a"].contributions)["dense"] == pytest.approx(RRF_UNIT)
    assert dict(items["b"].contributions)["dense"] == 0.0
    assert items["b"].agreement == 1


def test_rerank_rewards_cross_channel_agreement() -> None:
    """跨通道一致命中的条目在重排里被小幅上调."""
    scores = {"a": 0.05, "b": 0.05}
    items = {
        "a": HybridItem(
            id="a",
            fused=0.05,
            channels=("sparse", "dense"),
            contributions=(("sparse", 0.02),),
            agreement=2,
        ),
        "b": HybridItem(
            id="b",
            fused=0.05,
            channels=("sparse",),
            contributions=(("sparse", 0.02),),
            agreement=1,
        ),
    }

    reranked, detail = rerank(scores, items, {}, HybridPolicy())

    assert detail["a"].rerank_delta > 0
    assert detail["b"].rerank_delta == 0
    assert reranked["a"] > reranked["b"]


def test_rerank_penalizes_near_duplicate_evidence() -> None:
    """与更高排名条目近乎重复的候选在重排里被小幅下调."""
    scores = {"a": 0.05, "b": 0.049}
    items = {
        key: HybridItem(
            id=key,
            fused=score,
            channels=("sparse",),
            contributions=(("sparse", score),),
            agreement=1,
        )
        for key, score in scores.items()
    }

    reranked, detail = rerank(scores, items, {"a": ROOT_CAUSE, "b": ROOT_CAUSE}, HybridPolicy())

    assert detail["b"].rerank_delta < 0
    assert reranked["b"] < scores["b"]
    assert detail["a"].rerank_delta == 0


def test_rerank_delta_is_bounded() -> None:
    """重排的乘性调整幅度不超过配置上限, 不会把明显更差的证据顶上来."""
    policy = HybridPolicy(agreement_bonus=1.0, rerank_delta=0.1)
    scores = {"a": 0.05}
    items = {
        "a": HybridItem(
            id="a",
            fused=0.05,
            channels=("sparse", "dense", "anchor"),
            contributions=(("sparse", 0.02),),
            agreement=3,
        )
    }

    reranked, detail = rerank(scores, items, {}, policy)

    assert abs(detail["a"].rerank_delta) <= 0.1
    assert reranked["a"] <= scores["a"] * 1.1


def test_search_hybrid_reports_channel_contributions(tmp_path: Path) -> None:
    """混合检索返回每条证据的通道贡献、一致通道数与重排增量."""
    memory = _memory(tmp_path, _Embedder())
    try:
        result = memory.search_hybrid("缓存 revision 修复", "u", 5)
    finally:
        memory.close()

    assert result.hits
    assert result.dense_available is True
    assert "dense" in result.channels
    item = result.items[0]
    assert item.channels
    assert dict(item.contributions)
    assert item.agreement >= 1
    assert result.to_dict()["items"][0]["id"] == item.id


def test_search_hybrid_without_embedder_reports_dense_unavailable(tmp_path: Path) -> None:
    """没有嵌入器时如实报告稠密通道不可用, 不伪造相似度."""
    memory = _memory(tmp_path)
    try:
        result = memory.search_hybrid("缓存 revision 修复", "u", 3)
    finally:
        memory.close()

    assert result.dense_available is False
    assert "dense" not in result.channels
    assert all(item.dense_similarity is None for item in result.items)


def test_dense_min_similarity_filters_weak_matches(tmp_path: Path) -> None:
    """低于稠密相似度下限的候选不参与稠密打分."""
    memory = _memory(tmp_path, _DiscriminativeEmbedder())
    try:
        loose = memory.search_hybrid("缓存 revision 修复", "u", 3)
        strict = memory.search_hybrid(
            "缓存 revision 修复", "u", 3, hybrid=HybridPolicy(dense_min_similarity=0.5)
        )
    finally:
        memory.close()

    assert loose.dense_available is True
    assert strict.dense_available is True
    assert all(
        item.dense_similarity is None or item.dense_similarity >= 0.5 for item in strict.items
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dense_weight": -1.0},
        {"agreement_bonus": 2.0},
        {"redundancy_penalty": -0.1},
        {"rerank_delta": 1.5},
        {"rerank_depth": 0},
        {"rerank_depth": True},
        {"dense_min_similarity": 2.0},
    ],
)
def test_hybrid_policy_rejects_invalid_values(kwargs: dict[str, Any]) -> None:
    """越界的融合与重排参数被拒绝."""
    with pytest.raises(CodingMemoryValidationError):
        HybridPolicy(**kwargs)


def test_search_hybrid_rejects_invalid_top_k(tmp_path: Path) -> None:
    """top_k 必须是 1 到 50 的真整数."""
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.search_hybrid("缓存", "u", 0)
        with pytest.raises(CodingMemoryValidationError):
            memory.search_hybrid("缓存", "u", True)
        with pytest.raises(CodingMemoryValidationError):
            memory.search_hybrid("缓存", "u", 51)
    finally:
        memory.close()


def test_assemble_matches_score_and_pack_halves(tmp_path: Path) -> None:
    """拆成打分与装包两步后, 一次性 assemble 的结果保持不变."""
    memory = _memory(tmp_path, _Embedder())
    try:
        retriever = memory.retriever
        policy = memory.policy
        ranks, features = retriever.collect_candidates("缓存 revision 修复", "u", policy)

        whole = retriever.assemble("缓存 revision 修复", "u", 5, policy, dict(ranks), features, [])
        half_ranks = dict(ranks)
        snapshot = memory.store.load_snapshot("u")
        retriever.score_candidates(
            "缓存 revision 修复", "u", policy, half_ranks, features, snapshot
        )
        half = retriever.pack_hits("u", 5, policy, half_ranks, snapshot)
    finally:
        memory.close()

    assert [(hit.id, hit.score) for hit in whole] == [(hit.id, hit.score) for hit in half]


def test_coding_memory_mcp_hybrid_search_reports_breakdown(tmp_path: Path) -> None:
    """MCP 混合检索工具返回通道贡献与重排增量."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    memory.add("r1", "repo-a", "s1", [SourceMessage("assistant", ROOT_CAUSE)])
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dmsa_coding_memory_hybrid_search",
                "arguments": {"query": "缓存 revision 修复"},
            },
        }
    )

    result = server._call(request)["result"]
    text = result["content"][0]["text"]

    assert '"contributions"' in text
    assert '"rerank_delta"' in text
    assert '"dense_available": false' in text
    memory.close()


def test_coding_memory_mcp_hybrid_search_rejects_invalid_arguments(tmp_path: Path) -> None:
    """越界的 top_k 以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dmsa_coding_memory_hybrid_search",
                "arguments": {"query": "缓存", "top_k": True},
            },
        }
    )

    result = server._call(request)["result"]

    assert result["isError"] is True
    memory.close()
