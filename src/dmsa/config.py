# ruff: noqa: RUF002, RUF003
"""配置管理（PD7）。.

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
from dataclasses import replace
from pathlib import Path
from typing import cast

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from dmsa.config_models import (
    DMSAConfig,
    EmbedderConfig,
    GradientConfig,
    MCPServerConfig,
    StorageConfig,
)
from dmsa.errors import ConfigError

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


def _load_from_toml(path: Path) -> dict[str, object]:
    """从 TOML 文件加载配置。."""
    try:
        with path.open("rb") as fp:
            data = cast(dict[str, object], tomllib.load(fp))
            if not isinstance(data, dict):
                raise ConfigError("TOML root must be a table")
            return data
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            f"failed to load config from {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc


def _load_from_env() -> dict[str, object]:
    """从环境变量加载配置（仅覆盖存在环境变量的字段）。.

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
    """把环境变量字符串转成合适的 Python 值。."""
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
    """递归合并 dict；override 优先。."""
    result = dict(base)
    for key, value in override.items():
        previous = result.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            result[key] = _deep_merge(
                cast(dict[str, object], previous), cast(dict[str, object], value)
            )
        else:
            result[key] = value
    return result


def load_config(
    *,
    explicit: DMSAConfig | None = None,
    search_paths: tuple[str, ...] = _CONFIG_SEARCH_PATHS,
) -> DMSAConfig:
    """按优先级加载配置并返回 :class:`DMSAConfig` 实例。.

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
    """把 dict 转成 :class:`DMSAConfig`；忽略未知字段。."""
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
        fusion_backend=str(gradient_data.get("fusion_backend", "static")),
        llm_chat_model=str(gradient_data.get("llm_chat_model", "gpt-4o-mini")),
        llm_base_url=str(gradient_data.get("llm_base_url", "")),
        llm_api_key_env=str(gradient_data.get("llm_api_key_env", "OPENAI_API_KEY")),
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
    """基于现有 config 创建新 config（覆盖指定字段）。.

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
    "EmbedderConfig",
    "GradientConfig",
    "MCPServerConfig",
    "StorageConfig",
    "load_config",
    "merge_config",
    "replace",  # 重新导出，方便用户使用
]
