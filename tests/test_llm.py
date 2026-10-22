"""L2 真实 LLM 后端测试（全部 mock client，不需要 API key / 网络）。"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from dmsa.config import EmbedderConfig, load_config
from dmsa.errors import ConfigError, EmbeddingFailedError, FusionConflictError
from dmsa.gradient import (
    LLMFusion,
    OpenAIEmbedder,
    StaticFusion,
    build_embedder,
    make_sample,
)

# ============================================================================
# mock openai client
# ============================================================================


class _FakeEmbeddings:
    """伪造 client.embeddings。"""

    def __init__(self, dim: int = 8, fail: bool = False) -> None:
        self.dim = dim
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    def create(self, *, model: str, input: list[str]) -> Any:
        self.calls.append((model, list(input)))
        if self.fail:
            raise RuntimeError("api down")
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * self.dim) for _ in input]
        )


class _FakeCompletions:
    """伪造 client.chat.completions。"""

    def __init__(self, reply: str = "be concise and conclusion-first", fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("api down")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))]
        )


def _fake_client(*, dim: int = 8, reply: str = "be concise", fail: bool = False) -> Any:
    """组合一个伪造 openai client。"""
    return SimpleNamespace(
        embeddings=_FakeEmbeddings(dim=dim, fail=fail),
        chat=SimpleNamespace(completions=_FakeCompletions(reply=reply, fail=fail)),
    )


# ============================================================================
# OpenAIEmbedder
# ============================================================================


def test_openai_embedder_embed_returns_vector() -> None:
    """正常路径：返回 dim 维向量，model 传给 API。"""
    client = _fake_client(dim=8)
    emb = OpenAIEmbedder(client=client, model="m-embed", dim=8)
    vec = emb.embed("hello")
    assert len(vec) == 8
    assert emb.name == "openai"
    assert client.embeddings.calls[0][0] == "m-embed"


def test_openai_embedder_batch_single_call() -> None:
    """批量嵌入合并成一次 API 调用。"""
    client = _fake_client(dim=4)
    emb = OpenAIEmbedder(client=client, dim=4)
    vecs = emb.embed_batch(["a", "b", "c"])
    assert len(vecs) == 3
    assert len(client.embeddings.calls) == 1


def test_openai_embedder_api_error_raises() -> None:
    """API 异常包装成 :class:`EmbeddingFailedError`。"""
    emb = OpenAIEmbedder(client=_fake_client(fail=True), dim=8)
    with pytest.raises(EmbeddingFailedError):
        emb.embed("x")


def test_openai_embedder_dim_mismatch_raises() -> None:
    """返回维度与声明不一致时抛错（防配置漂移）。"""
    emb = OpenAIEmbedder(client=_fake_client(dim=8), dim=16)
    with pytest.raises(EmbeddingFailedError):
        emb.embed("x")


def test_openai_embedder_empty_batch() -> None:
    """空 batch 边界条件：返回空列表，不调 API。"""
    client = _fake_client()
    emb = OpenAIEmbedder(client=client, dim=8)
    assert emb.embed_batch([]) == []
    assert client.embeddings.calls == []


# ============================================================================
# LLMFusion
# ============================================================================


def test_llm_fusion_returns_llm_text() -> None:
    """正常路径：fused_text 来自 LLM，support_size / confidence 正确。"""
    fusion = LLMFusion(client=_fake_client(reply="be shorter"))
    samples = [
        make_sample(feedback_text="too verbose", severity=0.8),
        make_sample(feedback_text="answers drag on", severity=0.6),
    ]
    fused = fusion.fuse(3, samples)
    assert fused.fused_text == "be shorter"
    assert fused.support_size == 2
    assert fused.cluster_id == 3
    assert 0.0 <= fused.confidence <= 1.0


def test_llm_fusion_empty_samples_raises() -> None:
    """空样本列表抛 :class:`FusionConflictError`（与 StaticFusion 一致）。"""
    fusion = LLMFusion(client=_fake_client())
    with pytest.raises(FusionConflictError):
        fusion.fuse(0, [])


def test_llm_fusion_api_error_without_fallback_raises() -> None:
    """无 fallback 时 API 异常包装成 :class:`FusionConflictError`。"""
    fusion = LLMFusion(client=_fake_client(fail=True))
    with pytest.raises(FusionConflictError):
        fusion.fuse(0, [make_sample(feedback_text="x")])


def test_llm_fusion_api_error_with_fallback_degrades() -> None:
    """有 fallback 时降级为静态拼接（不中断反馈闭环）。"""
    fusion = LLMFusion(client=_fake_client(fail=True), fallback=StaticFusion())
    fused = fusion.fuse(0, [make_sample(feedback_text="too verbose")])
    assert "too verbose" in fused.fused_text
    assert "[cluster=" in fused.fused_text  # StaticFusion 的拼接格式


def test_llm_fusion_empty_llm_reply_raises() -> None:
    """LLM 返回空文本视为失败。"""
    fusion = LLMFusion(client=_fake_client(reply=""))
    with pytest.raises(FusionConflictError):
        fusion.fuse(0, [make_sample(feedback_text="x")])


# ============================================================================
# build_embedder 工厂 + client 构造错误
# ============================================================================


def test_build_embedder_hash_default() -> None:
    """默认 hash 后端，维度来自 config.dim。"""
    emb = build_embedder(EmbedderConfig(backend="hash", dim=64))
    assert emb.name == "hash"
    assert emb.dim == 64


def test_build_embedder_unknown_backend_raises() -> None:
    """未知 backend 抛 :class:`ConfigError`。"""
    with pytest.raises(ConfigError):
        build_embedder(EmbedderConfig(backend="no-such-backend"))


def test_build_embedder_sentence_transformers_not_implemented() -> None:
    """sentence-transformers 后端 v0.1 明确报错。"""
    with pytest.raises(ConfigError):
        build_embedder(EmbedderConfig(backend="sentence-transformers"))


def test_make_client_without_openai_package_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """未安装 openai 时给出安装提示（模拟 ImportError）。"""
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ConfigError, match="openai"):
        OpenAIEmbedder()


def test_make_client_without_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 API key 且 openai client 构造失败时包装成 :class:`ConfigError`。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # openai 包已安装时，OpenAI() 无 key 会抛 OpenAIError —— 应被包装
    with pytest.raises(ConfigError):
        LLMFusion()


# ============================================================================
# 配置：fusion_backend 校验 + env 覆盖
# ============================================================================


def test_config_fusion_backend_valid() -> None:
    """合法的 fusion_backend 通过校验。"""
    cfg = load_config(explicit=None, search_paths=())
    assert cfg.gradient.fusion_backend == "static"


def test_config_fusion_backend_invalid_raises() -> None:
    """非法 fusion_backend 抛 :class:`InvalidConfigValueError`。"""
    from dmsa.config import DMSAConfig, GradientConfig
    from dmsa.errors import InvalidConfigValueError

    with pytest.raises(InvalidConfigValueError):
        DMSAConfig(gradient=GradientConfig(fusion_backend="bogus")).validate()


def test_config_fusion_backend_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DMSA_GRADIENT__FUSION_BACKEND 环境变量覆盖默认值。"""
    monkeypatch.setenv("DMSA_GRADIENT__FUSION_BACKEND", "llm")
    monkeypatch.setenv("DMSA_GRADIENT__LLM_CHAT_MODEL", "test-model")
    cfg = load_config(search_paths=())
    assert cfg.gradient.fusion_backend == "llm"
    assert cfg.gradient.llm_chat_model == "test-model"
