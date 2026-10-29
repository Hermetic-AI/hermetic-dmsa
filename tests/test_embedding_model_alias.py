"""嵌入模型名别名与指纹隔离的回归测试."""

from __future__ import annotations

import pytest

from dmsa.errors import ConfigError
from dmsa.mcp_server.http.embedding import QwenTextEmbedding
from dmsa.mcp_server.http.runtime import EMBEDDING_MODELS, load_settings


def _embedder(model: str) -> QwenTextEmbedding:
    """构造一个不发起网络请求的适配器实例."""
    return QwenTextEmbedding(
        "https://example.com/embeddings",
        "unit-key",
        1024,
        model=model,
    )


def test_both_qwen_model_names_are_accepted() -> None:
    """两个 Qwen 模型名都要能配置, 否则线上既有环境会在启动时被拒."""
    assert "qwen3.7-text-embedding" in EMBEDDING_MODELS
    assert "qwen3.7-text-embedding-flash" in EMBEDDING_MODELS
    assert "text-embedding-v4" in EMBEDDING_MODELS


def test_different_model_names_keep_distinct_fingerprints() -> None:
    """两个 Qwen 模型名指向不同权重, 指纹必须不同, 否则向量会混用."""
    legacy = _embedder("qwen3.7-text-embedding")
    flash = _embedder("qwen3.7-text-embedding-flash")
    assert legacy.fingerprint != flash.fingerprint
    assert legacy.fingerprint == "qwen3.7-text-embedding:1024"
    assert flash.fingerprint == "qwen3.7-text-embedding-flash:1024"


def test_unknown_model_name_is_rejected() -> None:
    """未知模型名必须报错, 不能静默降级."""
    with pytest.raises(ConfigError):
        _embedder("some-other-model")


def test_runtime_accepts_legacy_qwen_name(monkeypatch) -> None:
    """线上以旧模型名配置时必须能正常启动."""
    monkeypatch.setenv("DMSA_MEMORY_API_KEY", "unit-key")
    monkeypatch.setenv("DMSA_EMBEDDING_BASE_URL", "https://example.com/embeddings")
    monkeypatch.setenv("DMSA_EMBEDDING_API_KEY", "unit-embed-key")
    monkeypatch.setenv("DMSA_EMBEDDING_MODEL", "qwen3.7-text-embedding")
    settings = load_settings("competition", dimension=1024)
    assert settings.embedding_model == "qwen3.7-text-embedding"


def test_both_qwen_names_dispatch_to_native_adapter() -> None:
    """两个 Qwen 名字都必须走 DashScope 原生适配器.

    落到 OpenAI 兼容适配器时, SDK 会自己给原生端点拼上 /embeddings, 请求直接
    400, 表现为 Add 与 Search 全部 503。
    """
    from dmsa.mcp_server.http.runtime import QWEN_MODELS

    assert "qwen3.7-text-embedding" in QWEN_MODELS
    assert "qwen3.7-text-embedding-flash" in QWEN_MODELS
    assert "text-embedding-v4" not in QWEN_MODELS
