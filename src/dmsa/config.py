"""配置管理（PD7）。

配置来源优先级（高→低）：
    1. 显式传入 :class:`DMSAConfig` 实例
    2. 环境变量（`DMSA_*` 前缀）
    3. ``dmsa.toml`` 文件（当前工作目录 / 用户主目录 / 系统配置目录）
    4. 代码默认值（:class:`DMSAConfig` 字段默认值）

环境变量映射规则：``DMSA_<SECTION>_<KEY>`` 全大写 + 下划线。
例：DMSA_STORAGE__PATH = "/tmp/dmsa.db" → DMSAConfig.storage.path
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from dmsa.errors import ConfigError, InvalidConfigValueError

# 配置搜索路径（按优先级从高到低）
_CONFIG_SEARCH_PATHS: tuple[str, ...] = (
    "./dmsa.toml",
    "./.dmsa.toml",
    "~/.config/hermetic-dmsa/config.toml",
    "~/.hermetic-dmsa.toml",
    "/etc/hermetic-dmsa/config.toml",
)

# 环境变量前缀
_ENV_PREFIX = "DMSA_"


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """存储后端配置。"""

    path: str = ":memory:"
    """SQLite 数据库路径；``":memory:"`` 表示内存（仅测试用）。"""

    enable_wal: bool = True
    """是否启用 WAL 模式（生产推荐 True）。"""

    backup_on_rollback: bool = True
    """rollback 时是否自动备份。"""


@dataclass(frozen=True, slots=True)
class EmbedderConfig:
    """嵌入器配置。"""

    backend: str = "hash"
    """``"hash"`` (零依赖) / ``"openai"`` / ``"sentence-transformers"``。"""

    dim: int = 256
    """嵌入维度；hash 后端固定 256，openai 固定 1536。"""

    model: str = ""
    """模型名；openai 默认为 ``text-embedding-3-small``。"""


@dataclass(frozen=True, slots=True)
class GradientConfig:
    """SGA 梯度聚合配置。"""

    clustering_backend: str = "fallback"
    """``"fallback"`` (零依赖单链接) / ``"hdbscan"``。"""

    min_cluster_size: int = 2
    """HDBSCAN / fallback 最小簇大小。"""

    conflict_threshold: float = 0.3
    """冲突检测的余弦距离阈值；> 此值视为真冲突。"""

    max_samples: int = 10_000
    """单次聚合最大样本数（防 DoS）。"""


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """MCP server 配置。"""

    max_feedback_length: int = 100_000
    """单条 feedback 文本最大字符数（防 DoS）。"""

    request_timeout_seconds: float = 30.0
    """单次工具调用超时。"""

    enable_metrics: bool = True
    """是否启用 metrics 收集。"""


@dataclass(frozen=True, slots=True)
class DMSAConfig:
    """hermetic-dmsa 全局配置。"""

    storage: StorageConfig = field(default_factory=StorageConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    gradient: GradientConfig = field(default_factory=GradientConfig)
    mcp_server: MCPServerConfig = field(default_factory=MCPServerConfig)

    def validate(self) -> None:
        """校验配置项值合法性。"""
        if self.embedder.dim <= 0:
            raise InvalidConfigValueError(
                f"embedder.dim must be positive, got {self.embedder.dim}",
                details={"field": "embedder.dim", "value": self.embedder.dim},
            )
        if self.gradient.min_cluster_size < 1:
            raise InvalidConfigValueError(
                f"gradient.min_cluster_size must be >= 1, got {self.gradient.min_cluster_size}",
                details={"field": "gradient.min_cluster_size"},
            )
        if not 0.0 <= self.gradient.conflict_threshold <= 1.0:
            raise InvalidConfigValueError(
                f"gradient.conflict_threshold must be in [0, 1], got {self.gradient.conflict_threshold}",
                details={"field": "gradient.conflict_threshold"},
            )
        if self.gradient.max_samples <= 0:
            raise InvalidConfigValueError(
                f"gradient.max_samples must be positive, got {self.gradient.max_samples}",
                details={"field": "gradient.max_samples"},
            )
        if self.mcp_server.max_feedback_length <= 0:
            raise InvalidConfigValueError(
                "mcp_server.max_feedback_length must be positive",
                details={"field": "mcp_server.max_feedback_length"},
            )
        if self.storage.path and self.storage.path != ":memory:":
            path = Path(self.storage.path).expanduser()
            parent = path.parent
            if not parent.exists():
                raise InvalidConfigValueError(
                    f"storage path parent does not exist: {parent}",
                    details={"storage.path": str(parent)},
                )

    def to_dict(self) -> dict[str, object]:
        """序列化为 dict。"""
        return {
            "storage": asdict(self.storage),
            "embedder": asdict(self.embedder),
            "gradient": asdict(self.gradient),
            "mcp_server": asdict(self.mcp_server),
        }


def _load_from_toml(path: Path) -> dict[str, object]:
    """从 TOML 文件加载配置。"""
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            f"failed to load config from {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc


def _load_from_env() -> dict[str, object]:
    """从环境变量加载配置（仅覆盖存在环境变量的字段）。

    命名约定：DMSA_<SECTION>__<KEY>（section 与 key 之间用 **双下划线** 分隔；
    单下划线保留作为字段名一部分，如 ``MIN_CLUSTER_SIZE`` -> ``min_cluster_size``）。
    """
    result: dict[str, object] = {}
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(_ENV_PREFIX):
            continue
        # DMSA_STORAGE__PATH -> storage.path
        # DMSA_GRADIENT__MIN_CLUSTER_SIZE -> gradient.min_cluster_size
        key = env_key[len(_ENV_PREFIX) :].lower().replace("__", ".")
        if "." not in key:
            continue
        section, field_name = key.split(".", 1)
        if section not in result:
            result[section] = {}
        section_dict = result[section]
        assert isinstance(section_dict, dict)
        section_dict[field_name] = _coerce_env_value(env_value)
    return result


def _coerce_env_value(value: str) -> object:
    """把环境变量字符串转成合适的 Python 值。"""
    lower = value.lower()
    if lower in ("true", "yes", "1"):
        return True
    if lower in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """递归合并 dict；override 优先。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    *,
    explicit: DMSAConfig | None = None,
    search_paths: tuple[str, ...] = _CONFIG_SEARCH_PATHS,
) -> DMSAConfig:
    """按优先级加载配置并返回 :class:`DMSAConfig` 实例。

    优先级：explicit > 环境变量 > TOML 文件（按 search_paths 顺序）> 默认值。
    """
    if explicit is not None:
        explicit.validate()
        return explicit

    merged: dict[str, object] = {}
    # TOML 文件（按 search_paths 顺序，后面的覆盖前面的）
    for candidate in search_paths:
        path = Path(candidate).expanduser()
        if path.is_file():
            file_cfg = _load_from_toml(path)
            merged = _deep_merge(merged, file_cfg)

    # 环境变量（覆盖 TOML）
    env_cfg = _load_from_env()
    merged = _deep_merge(merged, env_cfg)

    # 转成 DMSAConfig
    return _dict_to_config(merged)


def _dict_to_config(data: dict[str, object]) -> DMSAConfig:
    """把 dict 转成 :class:`DMSAConfig`；忽略未知字段。"""
    storage_data = data.get("storage", {})
    embedder_data = data.get("embedder", {})
    gradient_data = data.get("gradient", {})
    mcp_data = data.get("mcp_server", {})

    if not isinstance(storage_data, dict):
        storage_data = {}
    if not isinstance(embedder_data, dict):
        embedder_data = {}
    if not isinstance(gradient_data, dict):
        gradient_data = {}
    if not isinstance(mcp_data, dict):
        mcp_data = {}

    storage = StorageConfig(
        path=str(storage_data.get("path", ":memory:")),
        enable_wal=bool(storage_data.get("enable_wal", True)),
        backup_on_rollback=bool(storage_data.get("backup_on_rollback", True)),
    )
    embedder = EmbedderConfig(
        backend=str(embedder_data.get("backend", "hash")),
        dim=int(embedder_data.get("dim", 256)),
        model=str(embedder_data.get("model", "")),
    )
    gradient = GradientConfig(
        clustering_backend=str(gradient_data.get("clustering_backend", "fallback")),
        min_cluster_size=int(gradient_data.get("min_cluster_size", 2)),
        conflict_threshold=float(gradient_data.get("conflict_threshold", 0.3)),
        max_samples=int(gradient_data.get("max_samples", 10_000)),
    )
    mcp_server = MCPServerConfig(
        max_feedback_length=int(mcp_data.get("max_feedback_length", 100_000)),
        request_timeout_seconds=float(mcp_data.get("request_timeout_seconds", 30.0)),
        enable_metrics=bool(mcp_data.get("enable_metrics", True)),
    )
    config = DMSAConfig(
        storage=storage,
        embedder=embedder,
        gradient=gradient,
        mcp_server=mcp_server,
    )
    config.validate()
    return config


def merge_config(base: DMSAConfig, **overrides: object) -> DMSAConfig:
    """基于现有 config 创建新 config（覆盖指定字段）。

    用法：
        new_cfg = merge_config(cfg, **{"embedder.dim": 768})
    """
    data: dict[str, object] = base.to_dict()
    for key, value in overrides.items():
        if "." not in key:
            continue
        section, field_name = key.split(".", 1)
        if section not in data:
            continue
        section_dict = data[section]
        assert isinstance(section_dict, dict)
        section_dict[field_name] = value
    return _dict_to_config(data)


__all__ = [
    "DMSAConfig",
    "StorageConfig",
    "EmbedderConfig",
    "GradientConfig",
    "MCPServerConfig",
    "load_config",
    "merge_config",
    "replace",  # 重新导出，方便用户使用
]
