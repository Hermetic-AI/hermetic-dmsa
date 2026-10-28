"""pytest 公共 fixtures。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dmsa.config import DMSAConfig, EmbedderConfig, GradientConfig, MCPServerConfig, StorageConfig


@pytest.fixture
def tmp_storage_path(tmp_path: Path) -> str:
    """临时 SQLite 存储路径（由 pytest tmp_path 自动清理）。"""
    return str(tmp_path / "test_dmsa.sqlite")


@pytest.fixture
def test_config(tmp_storage_path: str) -> DMSAConfig:
    """测试用 DMSAConfig。"""
    return DMSAConfig(
        storage=StorageConfig(path=tmp_storage_path, enable_wal=True),
        embedder=EmbedderConfig(backend="hash", dim=64),
        gradient=GradientConfig(
            clustering_backend="fallback",
            min_cluster_size=2,
            conflict_threshold=0.3,
            max_samples=100,
        ),
        mcp_server=MCPServerConfig(
            max_feedback_length=10_000,
            request_timeout_seconds=5.0,
            enable_metrics=False,
        ),
    )


@pytest.fixture
def in_memory_config() -> DMSAConfig:
    """内存模式 config（最快，单元测试用）。"""
    return DMSAConfig(
        storage=StorageConfig(path=":memory:"),
        embedder=EmbedderConfig(backend="hash", dim=32),
        gradient=GradientConfig(
            clustering_backend="fallback",
            min_cluster_size=2,
        ),
    )
