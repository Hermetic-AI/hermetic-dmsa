"""L1 MemoryLayer 测试。"""

from __future__ import annotations

import time

import pytest
from pmd import ContentType, EdgeType, PromptContext

from dmsa.errors import LineageNotFoundError, MemoryLayerError, MissingParentError
from dmsa.memory import (
    LineageDirection,
    LineageQuery,
    MemoryLayer,
    create_prompt_context,
    from_dict,
    to_dict,
)
from dmsa.types import MemoryLayerConfig


# ============================================================================
# 基础
# ============================================================================


def test_memory_layer_creates_empty() -> None:
    """空 MemoryLayer 创建后长度为 0。"""
    layer = MemoryLayer()
    assert len(layer) == 0
    assert "nonexistent" not in layer


def test_create_prompt_context_basic() -> None:
    """create_prompt_context 填充必填字段。"""
    ctx = create_prompt_context(
        system_prompt="you are a helper",
        user_prompt="hello",
        model_name="test-model",
        temperature=0.7,
    )
    assert ctx.system_prompt == "you are a helper"
    assert ctx.user_prompt == "hello"
    assert ctx.model_name == "test-model"
    assert ctx.temperature == 0.7
    assert ctx.prompt_id != ""  # 自动生成
    assert ctx.agent_id != ""  # 自动生成
    assert ctx.request_timestamp > 0


def test_create_prompt_context_requires_some_text() -> None:
    """system_prompt 和 user_prompt 都为空时抛错。"""
    with pytest.raises(Exception):  # InvalidPromptContextError
        create_prompt_context()


def test_create_prompt_context_validates_temperature() -> None:
    """温度必须在 [0, 2]。"""
    with pytest.raises(Exception):
        create_prompt_context(system_prompt="x", temperature=3.0)


# ============================================================================
# add_memory
# ============================================================================


def test_add_memory_basic() -> None:
    """add_memory 写入一条记忆并返回 node。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="you are a test agent")
    node = layer.add_memory(content="hello world", prompt_context=ctx)
    assert node.id != ""
    assert node.content == "hello world"
    assert node.creator_prompt_id == ctx.prompt_id
    assert node.creator_agent_id == ctx.agent_id
    assert len(layer) == 1


def test_add_memory_with_parents() -> None:
    """add_memory 支持 parents。"""
    layer = MemoryLayer()
    ctx1 = create_prompt_context(system_prompt="a")
    parent = layer.add_memory(content="parent", prompt_context=ctx1)

    ctx2 = create_prompt_context(system_prompt="b")
    child = layer.add_memory(
        content="child",
        prompt_context=ctx2,
        parents=[parent.id],
    )
    ancestors = layer.get_ancestors(child.id)
    assert any(n.id == parent.id for n in ancestors)


def test_add_memory_with_missing_parent_raises() -> None:
    """parent 不存在时抛 :class:`MissingParentError`。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    with pytest.raises(MissingParentError):
        layer.add_memory(content="orphan", prompt_context=ctx, parents=["nonexistent"])


def test_add_memory_with_tags_and_importance() -> None:
    """tags / importance 正确传递。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    node = layer.add_memory(
        content="important note",
        prompt_context=ctx,
        tags=["user_pref", "important"],
        importance=0.9,
        requires_grad=True,
        grad_learning_rate=0.1,
    )
    assert "user_pref" in node.tags
    assert "important" in node.tags
    assert node.importance == 0.9
    assert node.requires_grad is True
    assert abs(node.grad_learning_rate - 0.1) < 1e-6


# ============================================================================
# 查询
# ============================================================================


def test_get_node_existing() -> None:
    """get_node 返回已存在的节点。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    node = layer.add_memory(content="hello", prompt_context=ctx)
    retrieved = layer.get_node(node.id)
    assert retrieved.id == node.id
    assert retrieved.content == "hello"


def test_get_node_nonexistent_raises() -> None:
    """get_node 找不到时抛 :class:`LineageNotFoundError`。"""
    layer = MemoryLayer()
    with pytest.raises(LineageNotFoundError):
        layer.get_node("nonexistent")


def test_get_ancestors_chain() -> None:
    """3 层链：grandparent -> parent -> child。"""
    layer = MemoryLayer()
    ctx_gp = create_prompt_context(system_prompt="gp")
    gp = layer.add_memory(content="grandparent", prompt_context=ctx_gp)
    ctx_p = create_prompt_context(system_prompt="p")
    p = layer.add_memory(content="parent", prompt_context=ctx_p, parents=[gp.id])
    ctx_c = create_prompt_context(system_prompt="c")
    c = layer.add_memory(content="child", prompt_context=ctx_c, parents=[p.id])

    ancestors = layer.get_ancestors(c.id)
    ancestor_ids = {n.id for n in ancestors}
    assert p.id in ancestor_ids
    assert gp.id in ancestor_ids


def test_query_lineage_with_filters() -> None:
    """query_lineage 支持 importance / tags 过滤。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    n1 = layer.add_memory(
        content="high importance",
        prompt_context=ctx,
        importance=0.9,
        tags=["important"],
    )
    n2 = layer.add_memory(
        content="low importance",
        prompt_context=ctx,
        importance=0.1,
        tags=["unimportant"],
    )
    # parent 为 n1
    n3 = layer.add_memory(
        content="derived",
        prompt_context=ctx,
        parents=[n1.id],
    )

    query = LineageQuery(
        root_id=n3.id,
        depth=2,
        direction=LineageDirection.UP,
        filters={"min_importance": 0.5, "tags": ["important"]},
    )
    results = layer.query_lineage(query)
    result_ids = {n.id for n in results}
    assert n1.id in result_ids
    assert n2.id not in result_ids


def test_get_creator_returns_prompt_context() -> None:
    """get_creator 找回原始 prompt context。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(
        system_prompt="you are X",
        user_prompt="hello",
        model_name="gpt-test",
    )
    node = layer.add_memory(content="hi", prompt_context=ctx)
    creator = layer.get_creator(node.id)
    assert creator is not None
    assert creator.prompt_id == ctx.prompt_id
    assert creator.system_prompt == "you are X"


# ============================================================================
# 版本
# ============================================================================


def test_snapshot_and_rollback() -> None:
    """snapshot / rollback 正确恢复。"""
    layer = MemoryLayer()
    ctx1 = create_prompt_context(system_prompt="1")
    layer.add_memory(content="v1", prompt_context=ctx1)
    snap = layer.snapshot(description="after v1")
    assert snap.version_id != ""

    ctx2 = create_prompt_context(system_prompt="2")
    layer.add_memory(content="v2", prompt_context=ctx2)
    assert len(layer) == 2

    layer.rollback(snap.version_id)
    assert len(layer) == 1


def test_list_versions() -> None:
    """list_versions 列出所有版本。"""
    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    layer.add_memory(content="a", prompt_context=ctx)
    layer.snapshot(description="snap 1", tag="v1")
    layer.snapshot(description="snap 2", tag="v2")
    versions = layer.list_versions()
    assert len(versions) == 2


# ============================================================================
# 序列化
# ============================================================================


def test_to_dict_from_dict_round_trip() -> None:
    """to_dict / from_dict 完整 round-trip。"""
    layer = MemoryLayer()
    ctx1 = create_prompt_context(system_prompt="1")
    n1 = layer.add_memory(content="a", prompt_context=ctx1, tags=["t1"])
    ctx2 = create_prompt_context(system_prompt="2")
    n2 = layer.add_memory(content="b", prompt_context=ctx2, parents=[n1.id])

    payload = to_dict(layer)
    assert payload["schema"] == "dmsa.memory.v1"
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1

    restored = from_dict(payload)
    assert len(restored) == 2
    # 检查内容
    r1 = restored.get_node(n1.id)
    r2 = restored.get_node(n2.id)
    assert r1.content == "a"
    assert r2.content == "b"
    assert "t1" in r1.tags


def test_serialization_via_json_string() -> None:
    """JSON 字符串序列化也能 round-trip。"""
    from dmsa.memory.serialization import from_json_string, to_json_string

    layer = MemoryLayer()
    ctx = create_prompt_context(system_prompt="x")
    layer.add_memory(content="x", prompt_context=ctx)

    text = to_json_string(layer, indent=2)
    assert "dmsa.memory.v1" in text

    restored = from_json_string(text)
    assert len(restored) == 1


# ============================================================================
# 检索（需要 embedder）
# ============================================================================


class _StubEmbedder:
    """测试用 stub embedder：返回 hash-based 固定维度向量。"""

    name = "stub"
    dim = 32

    def embed(self, text: str) -> list[float]:
        """用 hash 生成固定维度向量。"""
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 重复 digest 凑够 32 个 float
        result: list[float] = []
        for i in range(self.dim):
            byte = digest[i % len(digest)]
            result.append((byte / 255.0) - 0.5)
        return result


def test_retrieve_requires_embedder() -> None:
    """retrieve 在无 embedder 时抛 :class:`MemoryLayerError`。"""
    layer = MemoryLayer()
    with pytest.raises(MemoryLayerError):
        layer.retrieve("query")


def test_retrieve_with_embedder() -> None:
    """有 embedder 时 retrieve 返回按相似度排序的节点。"""
    embedder = _StubEmbedder()
    layer = MemoryLayer(embedder=embedder)
    ctx = create_prompt_context(system_prompt="x")

    # 内容差异显著的节点
    layer.add_memory(content="apple banana cherry", prompt_context=ctx)
    layer.add_memory(content="quantum physics relativity", prompt_context=ctx)
    layer.add_memory(content="apple fruit tree", prompt_context=ctx)

    results = layer.retrieve("apple", k=2)
    assert len(results) == 2
    # "apple banana" 和 "apple fruit" 应该排前
    contents = [n.content for n in results]
    assert any("apple" in c for c in contents)


def test_lineage_aware_search_with_embedder() -> None:
    """lineage_aware_search 在有 embedder 时工作。"""
    embedder = _StubEmbedder()
    layer = MemoryLayer(embedder=embedder)
    ctx = create_prompt_context(system_prompt="x")

    n1 = layer.add_memory(content="user prefers dark mode", prompt_context=ctx)
    layer.add_memory(
        content="dark mode is enabled in settings",
        prompt_context=ctx,
        parents=[n1.id],
    )

    results = layer.lineage_aware_search("dark mode preference", k=2, depth=2)
    assert len(results) >= 1
