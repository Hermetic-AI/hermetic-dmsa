"""MemoryLayer regression tests for persistence and active retrieval."""

from __future__ import annotations

import pytest
from pmd import PromptContext
from pmd.core.enums import NodeStatus
from pmd.store.sqlite_store import SQLiteStore

from dmsa.errors import MemoryLayerError, MissingParentError
from dmsa.memory import MemoryLayer, create_prompt_context
from dmsa.types import MemoryLayerConfig


class ExactEmbedder:
    """为测试提供确定性嵌入。"""

    name = "exact"

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "match" in text else [0.0, 1.0]


def _context() -> PromptContext:
    return create_prompt_context(user_prompt="test")


def test_configured_sqlite_survives_reopen(tmp_path) -> None:
    """配置的 SQLite 文件在新 layer 中仍可读取节点。"""
    path = tmp_path / "memory.sqlite"
    first = MemoryLayer(config=MemoryLayerConfig(storage_path=str(path)))
    node = first.add_memory(content="persisted", prompt_context=_context())
    reopened = MemoryLayer(config=MemoryLayerConfig(storage_path=str(path)))
    assert isinstance(first.store, SQLiteStore)
    assert reopened.get_node(node.id).content == "persisted"


def test_explicit_store_wins_over_configured_path(tmp_path) -> None:
    """调用方传入的 store 优先于 storage_path。"""
    store = SQLiteStore(":memory:")
    layer = MemoryLayer(
        config=MemoryLayerConfig(storage_path=str(tmp_path / "ignored.sqlite")),
        store=store,
    )
    assert layer.store is store
    assert not (tmp_path / "ignored.sqlite").exists()


def test_active_retrieval_does_not_starve_after_filtering() -> None:
    """删除及过期节点不会挤占 top-k 的位置。"""
    layer = MemoryLayer(embedder=ExactEmbedder())
    ctx = _context()
    deleted = layer.add_memory(content="match deleted", prompt_context=ctx)
    layer.add_memory(content="match expired", prompt_context=ctx, ttl=-1)
    active = layer.add_memory(content="match active", prompt_context=ctx)
    layer._dag._nodes[deleted.id] = deleted.model_copy(update={"status": NodeStatus.DELETED})
    assert [node.id for node in layer.retrieve("match", k=1)] == [active.id]


def test_zero_learning_rate_rejects_successor() -> None:
    """零学习率节点是反向传播硬边界。"""
    layer = MemoryLayer()
    node = layer.add_memory(
        content="fixed", prompt_context=_context(), requires_grad=True, grad_learning_rate=0
    )
    with pytest.raises(MemoryLayerError):
        layer.apply_text_gradient(
            node_id=node.id, gradient_text="change", prompt_context=_context()
        )


def test_missing_parent_remains_typed_error() -> None:
    """缺失父节点保留 MissingParentError 类型。"""
    with pytest.raises(MissingParentError):
        MemoryLayer().add_memory(content="orphan", prompt_context=_context(), parents=["absent"])


def test_lineage_expansion_excludes_deleted_and_expired_parents() -> None:
    layer = MemoryLayer(embedder=ExactEmbedder())
    ctx = _context()
    deleted = layer.add_memory(content="other", prompt_context=ctx, importance=1)
    expired = layer.add_memory(content="other", prompt_context=ctx, importance=1, ttl=-1)
    seed = layer.add_memory(content="match", prompt_context=ctx, parents=[deleted.id, expired.id])
    layer.dag._nodes[deleted.id] = deleted.model_copy(update={"status": NodeStatus.DELETED})
    assert [node.id for node in layer.lineage_aware_search("match", k=10)] == [seed.id]


def test_storage_initialization_failure_is_typed(tmp_path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    with pytest.raises(MemoryLayerError, match="initialize memory storage"):
        MemoryLayer(config=MemoryLayerConfig(storage_path=str(occupied / "db.sqlite")))


def test_sqlite_snapshot_serialization_is_detached(tmp_path) -> None:
    path = tmp_path / "db.sqlite"
    layer = MemoryLayer(config=MemoryLayerConfig(storage_path=str(path)))
    node = layer.add_memory(content="original", prompt_context=_context())
    layer.add_memory(content="child", prompt_context=_context(), parents=[node.id])
    layer = MemoryLayer(config=MemoryLayerConfig(storage_path=str(path)))
    child = next(item for item in layer.dag._nodes.values() if item.content == "child")
    snapshot = layer.snapshot()
    layer.add_memory(content="later", prompt_context=_context())
    with pytest.raises(MemoryLayerError, match="unsupported for SQLite"):
        layer.rollback(snapshot.version_id)
    restored = MemoryLayer.import_state(layer.export_state())
    assert len(restored) == 3
    assert restored.get_node(node.id).content == "original"
    assert restored.get_ancestors(child.id, depth=1)[0].id == node.id
    restored.add_memory(content="detached", prompt_context=_context())
    assert len(MemoryLayer(config=MemoryLayerConfig(storage_path=str(path)))) == 3


def test_detached_import_restores_parent_edges() -> None:
    """导入导出恢复父边, 且 rollback 只作用于内存 store。"""
    layer = MemoryLayer()
    parent = layer.add_memory(content="parent", prompt_context=_context())
    child = layer.add_memory(content="child", prompt_context=_context(), parents=[parent.id])
    restored = MemoryLayer.import_state(layer.export_state())
    assert restored.get_ancestors(child.id, depth=1)[0].id == parent.id


def test_import_legacy_parent_edges_in_reverse_node_order() -> None:
    """旧格式只有 node.parents 且节点顺序反转时仍先恢复父节点。"""
    layer = MemoryLayer()
    parent = layer.add_memory(content="parent", prompt_context=_context())
    child = layer.add_memory(content="child", prompt_context=_context(), parents=[parent.id])
    payload = layer.export_state()
    payload.pop("edges")
    payload["nodes"] = dict(reversed(list(payload["nodes"].items())))
    restored = MemoryLayer.import_state(payload)
    assert restored.get_ancestors(child.id, depth=1)[0].id == parent.id


def test_cli_passes_storage_config(tmp_path, monkeypatch) -> None:
    from dmsa.mcp_server import cli

    captured = []
    original = cli.MemoryLayer

    def capture_layer(**kwargs):
        layer = original(**kwargs)
        captured.append(layer)
        return layer

    async def stop_server(self):
        return None

    monkeypatch.setattr(cli, "MemoryLayer", capture_layer)
    monkeypatch.setattr(cli.MCPServer, "run_stdio", stop_server)
    path = tmp_path / "cli.sqlite"
    assert cli.main(["--storage", str(path)]) == 0
    assert captured[0]._config.storage_path == str(path)
    assert path.is_file()
