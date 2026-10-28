"""真实 LLM 后端：OpenAI 嵌入器 + LLM 融合器（可选依赖 ``openai``）。

默认零依赖实现（:class:`HashEmbedder` / :class:`StaticFusion`）无语义信号，
只够测试和演示。本模块提供生产级替代品，安装::

    pip install hermetic-dmsa[llm]

两个类都支持注入 client（测试传 mock；生产不传则惰性 import openai 自动构造）：

    >>> from dmsa.gradient.llm import LLMFusion, OpenAIEmbedder
    >>> embedder = OpenAIEmbedder()  # 读 OPENAI_API_KEY
    >>> fusion = LLMFusion(fallback=StaticFusion())  # API 失败时降级

依赖规则：L2 模块，只 import L1/L0 + errors/observability；openai 惰性导入。
"""

from __future__ import annotations

import os
import statistics
from typing import Any

from dmsa.errors import ConfigError, EmbeddingFailedError, FusionConflictError
from dmsa.gradient.fusion import FusionClient
from dmsa.observability import get_logger
from dmsa.types import FusedGradient, GradientSample

__all__ = ["LLMFusion", "OpenAIEmbedder", "build_embedder"]

_logger = get_logger("dmsa.gradient.llm")

_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_DEFAULT_EMBEDDING_DIM = 1536
_DEFAULT_CHAT_MODEL = "gpt-4o-mini"

_FUSION_SYSTEM_PROMPT = (
    "You merge multiple user feedback items about an AI agent's behavior into "
    "a single concise, actionable improvement directive. Deduplicate repeated "
    "points, resolve wording conflicts, keep it under 3 sentences. "
    "Output only the directive, no preamble."
)


def _make_openai_client(api_key_env: str, base_url: str) -> Any:
    """惰性 import openai 并构造 client；失败时给出可操作的错误提示。"""
    try:
        import openai
    except ImportError as exc:
        raise ConfigError(
            "openai package not installed",
            details={"hint": "pip install hermetic-dmsa[llm]"},
        ) from exc
    kwargs: dict[str, Any] = {}
    api_key = os.environ.get(api_key_env, "")
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    try:
        return openai.OpenAI(**kwargs)
    except Exception as exc:
        raise ConfigError(
            f"failed to construct openai client: {exc}",
            details={
                "hint": f"set ${api_key_env} or pass base_url for a compatible endpoint",
            },
        ) from exc


def _samples_confidence(samples: list[GradientSample]) -> float:
    """与 :class:`StaticFusion` 一致的确定性置信度公式（保证可复现）。"""
    if len(samples) == 1:
        return 1.0
    return max(0.0, 1.0 - statistics.variance(s.severity for s in samples))


class OpenAIEmbedder:
    """OpenAI 嵌入器（兼容任何 OpenAI 协议的 endpoint）。

    Args:
        client: 注入的 openai client（测试用 mock）；None 则自动构造。
        model: 嵌入模型名。
        dim: 期望维度；API 返回向量维度不等于它时抛错（防配置漂移）。
        api_key_env: API key 环境变量名。
        base_url: 自定义 endpoint（OpenAI 兼容服务，如 vLLM / 代理）。
    """

    name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str = _DEFAULT_EMBEDDING_MODEL,
        dim: int = _DEFAULT_EMBEDDING_DIM,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "",
    ) -> None:
        """初始化 OpenAI 兼容嵌入客户端, 缺省参数取自环境变量。"""
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self.model = model
        self.dim = dim
        self._client = client if client is not None else _make_openai_client(api_key_env, base_url)

    def embed(self, text: str) -> list[float]:
        """嵌入单段文本。"""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（单次 API 调用）。"""
        if not texts:
            return []
        try:
            resp = self._client.embeddings.create(model=self.model, input=list(texts))
            vectors = [list(item.embedding) for item in resp.data]
        except Exception as exc:
            raise EmbeddingFailedError(
                f"openai embedding failed: {exc}",
                details={"model": self.model, "batch_size": len(texts)},
            ) from exc
        for vec in vectors:
            if len(vec) != self.dim:
                raise EmbeddingFailedError(
                    f"embedding dim mismatch: expected {self.dim}, got {len(vec)}",
                    details={"model": self.model, "expected": self.dim, "got": len(vec)},
                )
        return vectors


class LLMFusion:
    """LLM 融合器：把簇内多条反馈改写成一条可执行指令。

    与 :class:`StaticFusion` 的差别：``fused_text`` 由 LLM 生成（去重、
    消解措辞冲突），而不是原样拼接。``confidence`` 仍用确定性的严重度
    公式（与 StaticFusion 一致），保证同样输入下指标可复现。

    Args:
        client: 注入的 openai client（测试用 mock）；None 则自动构造。
        model: chat 模型名。
        fallback: API 失败时的降级融合器（如 ``StaticFusion()``）；
            None = 直接抛 :class:`FusionConflictError`。
        temperature: 采样温度，默认 0.0（确定性）。
        api_key_env: API key 环境变量名。
        base_url: 自定义 endpoint（OpenAI 兼容服务）。
    """

    name = "llm"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str = _DEFAULT_CHAT_MODEL,
        fallback: FusionClient | None = None,
        temperature: float = 0.0,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "",
    ) -> None:
        """初始化 LLM 融合客户端, 缺省参数取自环境变量。"""
        self.model = model
        self.temperature = temperature
        self._fallback = fallback
        self._client = client if client is not None else _make_openai_client(api_key_env, base_url)

    def fuse(
        self,
        cluster_id: int,
        samples: list[GradientSample],
    ) -> FusedGradient:
        """融合一个簇：LLM 改写 fused_text，confidence 走确定性公式。"""
        if not samples:
            raise FusionConflictError(
                "cannot fuse empty sample list",
                details={"cluster_id": cluster_id},
            )
        try:
            fused_text = self._call_llm(samples)
        except Exception as exc:
            if self._fallback is not None:
                _logger.warning(
                    "llm fusion failed, falling back",
                    fusion=self._fallback.name,
                    error=str(exc),
                )
                return self._fallback.fuse(cluster_id, samples)
            raise FusionConflictError(
                f"llm fusion failed: {exc}",
                details={"cluster_id": cluster_id, "model": self.model},
            ) from exc
        return FusedGradient(
            cluster_id=cluster_id,
            fused_text=fused_text,
            support_size=len(samples),
            confidence=_samples_confidence(samples),
            has_conflict=False,
            branch_conditions=(),
        )

    def _call_llm(self, samples: list[GradientSample]) -> str:
        """调用 chat completions 生成融合指令。"""
        lines = [
            f"{i}. [sev={s.severity:.2f} source={s.source}] {s.feedback_text}"
            for i, s in enumerate(samples, 1)
        ]
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": _FUSION_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise FusionConflictError(
                "llm returned empty fusion text",
                details={"model": self.model, "sample_count": len(samples)},
            )
        _logger.info("llm fusion ok", model=self.model, sample_count=len(samples))
        return text


def build_embedder(config: Any) -> Any:
    """按 :class:`dmsa.config.EmbedderConfig` 构造嵌入器实例。

    - ``backend="hash"`` → :class:`HashEmbedder`（零依赖，用 config.dim）
    - ``backend="openai"`` → :class:`OpenAIEmbedder`（固定 1536 维，
      config.model 可覆盖模型名；config.dim 仅 hash 后端使用）
    - ``backend="sentence-transformers"`` → v0.1 未实现，明确报错
    """
    from dmsa.gradient.embedding import HashEmbedder

    backend = getattr(config, "backend", "hash")
    if backend == "hash":
        return HashEmbedder(dim=int(getattr(config, "dim", 256)))
    if backend == "openai":
        model = getattr(config, "model", "") or _DEFAULT_EMBEDDING_MODEL
        return OpenAIEmbedder(model=model)
    if backend == "sentence-transformers":
        raise ConfigError(
            "sentence-transformers backend not implemented in v0.1",
            details={"hint": "use backend='hash' (zero-dep) or 'openai'"},
        )
    raise ConfigError(
        f"unknown embedder backend: {backend!r}",
        details={"backend": backend, "known": ["hash", "openai", "sentence-transformers"]},
    )
