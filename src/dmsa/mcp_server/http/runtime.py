"""HTTP 部署配置与挂载密钥读取。."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dmsa.errors import ConfigError

# 可选的嵌入模型名。qwen3.7-text-embedding 与 qwen3.7-text-embedding-flash 是
# 两个不同模型(实测同一条文本的向量余弦仅 0.065), 因此各自保留独立指纹,
# 不能互相替换: 已写入的向量只在其指纹下可被稠密通道召回。
QWEN_MODELS = frozenset({"qwen3.7-text-embedding", "qwen3.7-text-embedding-flash"})
# OpenAI 兼容协议那一类走 /v1/embeddings 路径, 与 DashScope 原生端点不同。
OPENAI_MODELS = frozenset({"text-embedding-v4"})
EMBEDDING_MODELS = QWEN_MODELS | OPENAI_MODELS


@dataclass(frozen=True)
class RuntimeSettings:
    """保存已校验配置并从 repr 中隐藏凭据及端点。."""

    api_key: str = field(repr=False)
    endpoint: str | None = field(default=None, repr=False)
    embedding_key: str | None = field(default=None, repr=False)
    dimension: int = 1536
    embedding_model: str | None = None


def read_secret(name: str) -> str:
    """读取环境变量或对应的 _FILE 文件，拒绝歧义和空值。."""
    value = os.environ.get(name, "")
    filename = os.environ.get(f"{name}_FILE", "")
    if value.strip() and filename.strip():
        raise ConfigError(f"set only one of {name} or {name}_FILE")
    if filename.strip():
        try:
            value = Path(filename).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ConfigError(f"cannot read {name}_FILE") from exc
    if not value.strip():
        raise ConfigError(f"required environment variable is unset: {name}")
    return value


def load_settings(
    profile: str,
    api_key_env: str = "DMSA_MEMORY_API_KEY",
    endpoint_env: str = "DMSA_EMBEDDING_BASE_URL",
    dimension: int = 1536,
) -> RuntimeSettings:
    """启动前验证 profile，比赛模式必须显式配置模型与服务。."""
    api_key = read_secret(api_key_env)
    if profile == "sparse-dev":
        return RuntimeSettings(api_key=api_key)
    if profile != "competition":
        raise ConfigError("unsupported HTTP profile")
    if type(dimension) is not int or dimension <= 0:
        raise ConfigError("embedding dimension must be positive")
    endpoint = os.environ.get(endpoint_env, "").strip()
    try:
        parsed = urlsplit(endpoint)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (parsed.port is None or 0 < parsed.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError(f"required valid HTTP endpoint: {endpoint_env}")
    embedding_model = os.environ.get("DMSA_EMBEDDING_MODEL", "").strip()
    if embedding_model not in EMBEDDING_MODELS:
        raise ConfigError(
            "DMSA_EMBEDDING_MODEL must be one of " + ", ".join(sorted(EMBEDDING_MODELS))
        )
    embedding_key = read_secret("DMSA_EMBEDDING_API_KEY")
    return RuntimeSettings(api_key, endpoint, embedding_key, dimension, embedding_model)
