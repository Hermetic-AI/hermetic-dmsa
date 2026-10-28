"""全局配置数据模型。."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from dmsa.errors import InvalidConfigValueError


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """存储后端配置。."""

    path: str = ":memory:"
    enable_wal: bool = True
    backup_on_rollback: bool = True


@dataclass(frozen=True, slots=True)
class EmbedderConfig:
    """嵌入器配置。."""

    backend: str = "hash"
    dim: int = 256
    model: str = ""


@dataclass(frozen=True, slots=True)
class GradientConfig:
    """梯度聚合配置。."""

    clustering_backend: str = "fallback"
    min_cluster_size: int = 2
    conflict_threshold: float = 0.3
    max_samples: int = 10_000
    fusion_backend: str = "static"
    llm_chat_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    llm_api_key_env: str = "OPENAI_API_KEY"


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """MCP server 配置。."""

    max_feedback_length: int = 100_000
    request_timeout_seconds: float = 30.0
    enable_metrics: bool = True


@dataclass(frozen=True, slots=True)
class DMSAConfig:
    """全局配置。."""

    storage: StorageConfig = field(default_factory=StorageConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    gradient: GradientConfig = field(default_factory=GradientConfig)
    mcp_server: MCPServerConfig = field(default_factory=MCPServerConfig)

    def validate(self) -> None:
        """校验常用边界及数据库目录。."""
        if self.embedder.dim <= 0:
            raise InvalidConfigValueError("embedder.dim must be positive")
        if self.gradient.min_cluster_size < 1:
            raise InvalidConfigValueError("gradient.min_cluster_size must be positive")
        if self.gradient.max_samples <= 0:
            raise InvalidConfigValueError("gradient.max_samples must be positive")
        if self.mcp_server.max_feedback_length <= 0:
            raise InvalidConfigValueError("mcp_server.max_feedback_length must be positive")
        if not 0 <= self.gradient.conflict_threshold <= 1:
            raise InvalidConfigValueError("conflict_threshold must be in [0, 1]")
        if self.gradient.fusion_backend not in ("static", "rule-based", "llm"):
            raise InvalidConfigValueError("invalid fusion_backend")
        if self.storage.path and self.storage.path != ":memory:":
            parent = Path(self.storage.path).expanduser().parent
            if not parent.exists():
                raise InvalidConfigValueError(f"storage path parent does not exist: {parent}")

    def to_dict(self) -> dict[str, object]:
        """序列化配置。."""
        return {
            "storage": asdict(self.storage),
            "embedder": asdict(self.embedder),
            "gradient": asdict(self.gradient),
            "mcp_server": asdict(self.mcp_server),
        }
