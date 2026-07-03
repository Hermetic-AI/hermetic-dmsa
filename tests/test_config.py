"""配置管理测试。"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from dmsa.config import (
    DMSAConfig,
    EmbedderConfig,
    GradientConfig,
    MCPServerConfig,
    StorageConfig,
    load_config,
    merge_config,
)
from dmsa.errors import ConfigError, InvalidConfigValueError


def test_default_config_valid() -> None:
    """默认配置应通过 validate。"""
    config = DMSAConfig()
    config.validate()  # 不抛异常


def test_explicit_config_takes_precedence() -> None:
    """显式传入 config 不受环境变量影响。"""
    explicit = DMSAConfig(embedder=EmbedderConfig(dim=512))
    result = load_config(explicit=explicit)
    assert result.embedder.dim == 512


def test_invalid_dim_raises() -> None:
    """embedder.dim <= 0 抛错。"""
    with pytest.raises(InvalidConfigValueError) as exc_info:
        config = DMSAConfig(embedder=EmbedderConfig(dim=0))
        config.validate()
    assert "embedder.dim" in str(exc_info.value)


def test_invalid_conflict_threshold_raises() -> None:
    """conflict_threshold 必须在 [0, 1]。"""
    with pytest.raises(InvalidConfigValueError):
        config = DMSAConfig(
            gradient=GradientConfig(conflict_threshold=1.5)
        )
        config.validate()


def test_invalid_min_cluster_size_raises() -> None:
    """min_cluster_size 必须 >= 1。"""
    with pytest.raises(InvalidConfigValueError):
        config = DMSAConfig(gradient=GradientConfig(min_cluster_size=0))
        config.validate()


def test_invalid_storage_path_raises() -> None:
    """storage.path 的父目录不存在时抛错。"""
    with pytest.raises(InvalidConfigValueError):
        config = DMSAConfig(
            storage=StorageConfig(path="/nonexistent/dir/file.db")
        )
        config.validate()


def test_load_from_toml(tmp_path: Path) -> None:
    """从 TOML 文件加载配置。"""
    toml_file = tmp_path / "dmsa.toml"
    toml_file.write_text(
        textwrap.dedent(
            """\
            [embedder]
            dim = 768
            backend = "openai"

            [gradient]
            min_cluster_size = 3
            """
        ),
        encoding="utf-8",
    )
    # 使用 search_paths 包含 tmp_path
    config = load_config(search_paths=(str(toml_file),))
    assert config.embedder.dim == 768
    assert config.embedder.backend == "openai"
    assert config.gradient.min_cluster_size == 3


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量 DMSA_<SECTION>_<KEY> 覆盖。"""
    monkeypatch.setenv("DMSA_EMBEDDER__DIM", "1024")
    monkeypatch.setenv("DMSA_GRADIENT__MIN_CLUSTER_SIZE", "5")
    config = load_config(search_paths=())  # 不读 TOML
    assert config.embedder.dim == 1024
    assert config.gradient.min_cluster_size == 5


def test_env_var_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量值自动转 int / float / bool。"""
    monkeypatch.setenv("DMSA_EMBEDDER__DIM", "256")
    monkeypatch.setenv("DMSA_GRADIENT__CONFLICT_THRESHOLD", "0.5")
    monkeypatch.setenv("DMSA_MCP_SERVER__ENABLE_METRICS", "true")
    config = load_config(search_paths=())
    assert isinstance(config.embedder.dim, int)
    assert isinstance(config.gradient.conflict_threshold, float)
    assert isinstance(config.mcp_server.enable_metrics, bool)


def test_merge_config_overrides_field() -> None:
    """merge_config 可以覆盖单字段。"""
    base = DMSAConfig()
    new_cfg = merge_config(base, **{"embedder.dim": 1024})
    assert new_cfg.embedder.dim == 1024
    # 其它字段不变
    assert new_cfg.storage.path == base.storage.path


def test_to_dict_round_trip() -> None:
    """to_dict 输出符合预期结构。"""
    config = DMSAConfig(embedder=EmbedderConfig(dim=512))
    d = config.to_dict()
    assert d["embedder"]["dim"] == 512
    assert "storage" in d
    assert "gradient" in d
    assert "mcp_server" in d


def test_load_missing_toml_uses_default() -> None:
    """TOML 不存在时使用默认值。"""
    config = load_config(search_paths=("/nonexistent/dmsa.toml",))
    assert config.embedder.dim == 256  # 默认值
