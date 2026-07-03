"""L3 Tool-Use BP 测试。"""

from __future__ import annotations

import pytest

from dmsa.errors import (
    ProjectionRollbackError,
    SchemaValidationError,
    ToolBPError,
    UnsupportedEditError,
)
from dmsa.toolbp import (
    EditOp,
    ToolBPEngine,
    ToolCallCritic,
    ToolCallRecord,
    ToolSchemaNode,
    UpdateLeafOp,
    UpdateRequiredOp,
    make_call_record,
    make_schema_node,
)
from dmsa.toolbp.edit_ops import apply_edit_op
from dmsa.toolbp.ast_projection import (
    SchemaNode,
    dump_schema,
    find_node,
    iter_nodes,
    parse_schema,
)


# ============================================================================
# ast_projection
# ============================================================================


def test_parse_simple_object_schema() -> None:
    """解析最简 object schema。"""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }
    root = parse_schema(schema)
    assert root.schema_type == "object"
    assert "name" in root.properties
    assert "age" in root.properties
    assert root.properties["name"].required is True
    assert root.properties["age"].required is False


def test_parse_nested_schema() -> None:
    """解析嵌套 object schema。"""
    schema = {
        "type": "object",
        "properties": {
            "address": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "city name"},
                },
            },
        },
    }
    root = parse_schema(schema)
    city_node = find_node(root, ("properties", "address", "properties", "city"))
    assert city_node is not None
    assert city_node.description == "city name"


def test_parse_array_schema() -> None:
    """解析 array schema。"""
    schema = {"type": "array", "items": {"type": "string"}}
    root = parse_schema(schema)
    assert root.schema_type == "array"
    assert root.items is not None
    assert root.items.schema_type == "string"


def test_dump_schema_round_trip() -> None:
    """dump_schema 输出与原 schema 等价。"""
    schema = {
        "type": "object",
        "description": "root",
        "properties": {
            "x": {"type": "string", "description": "x field"},
        },
        "required": ["x"],
    }
    root = parse_schema(schema)
    dumped = dump_schema(root)
    assert dumped["type"] == "object"
    assert dumped["description"] == "root"
    assert dumped["required"] == ["x"]
    assert dumped["properties"]["x"]["description"] == "x field"


def test_iter_nodes_visits_all() -> None:
    """iter_nodes 遍历所有节点。"""
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "object", "properties": {"c": {"type": "integer"}}},
        },
    }
    root = parse_schema(schema)
    nodes = list(iter_nodes(root))
    # root + a + b + c = 4
    assert len(nodes) == 4


def test_find_node_returns_none_for_missing() -> None:
    """find_node 找不到时返回 None。"""
    root = parse_schema({"type": "object", "properties": {}})
    assert find_node(root, ("properties", "missing")) is None


# ============================================================================
# edit_ops
# ============================================================================


def test_update_leaf_op_changes_description() -> None:
    """UpdateLeafOp 修改 description。"""
    root = parse_schema(
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "old"}},
        }
    )
    op = UpdateLeafOp(field_path=("name",), new_description="new desc")
    new_root = apply_edit_op(op, root)
    node = find_node(new_root, ("properties", "name"))
    assert node is not None
    assert node.description == "new desc"


def test_update_leaf_op_keeps_unchanged_fields() -> None:
    """UpdateLeafOp 只修改指定字段，其他不变。"""
    root = parse_schema(
        {
            "type": "object",
            "properties": {"x": {"type": "string", "description": "old"}},
        }
    )
    op = UpdateLeafOp(field_path=("x",), new_description="new")
    new_root = apply_edit_op(op, root)
    node = find_node(new_root, ("properties", "x"))
    assert node is not None
    assert node.schema_type == "string"  # 不变


def test_update_required_op_toggles_required() -> None:
    """UpdateRequiredOp 修改 required 标记。"""
    root = parse_schema(
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
        }
    )
    op = UpdateRequiredOp(field_path=("x",), required=True)
    new_root = apply_edit_op(op, root)
    dumped = dump_schema(new_root)
    assert "x" in dumped["required"]


def test_update_required_propagates_to_dump() -> None:
    """UpdateRequiredOp 影响 dump 输出。"""
    root = parse_schema({"type": "object", "properties": {"x": {"type": "string"}}})
    op = UpdateRequiredOp(field_path=("x",), required=True)
    new_root = apply_edit_op(op, root)
    dumped = dump_schema(new_root)
    assert dumped.get("required") == ["x"]


def test_validate_edit_op_rejects_unknown_field() -> None:
    """目标节点不存在抛 :class:`SchemaValidationError`。"""
    root = parse_schema({"type": "object", "properties": {}})
    op = UpdateLeafOp(field_path=("missing",), new_description="x")
    with pytest.raises(SchemaValidationError):
        apply_edit_op(op, root)


def test_validate_edit_op_rejects_invalid_type() -> None:
    """非法 type 抛错。"""
    root = parse_schema({"type": "object", "properties": {"x": {"type": "string"}}})
    op = UpdateLeafOp(field_path=("x",), new_type="not-a-type")
    with pytest.raises(SchemaValidationError):
        apply_edit_op(op, root)


def test_update_required_rejects_non_object() -> None:
    """UpdateRequiredOp 不能用在非 property 节点（items 子节点 / root）。"""
    root = parse_schema(
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "items": {"type": "string"},  # 不合法但用于测错误路径
        }
    )
    # 对 items 节点用 UpdateRequiredOp（path 不是 properties.x 形式）
    op = UpdateRequiredOp(field_path=("items",), required=True)
    with pytest.raises(SchemaValidationError):
        apply_edit_op(op, root)


# ============================================================================
# ToolCallCritic
# ============================================================================


def test_critic_on_success() -> None:
    """成功调用产生低 severity 反馈。"""
    critic = ToolCallCritic()
    record = make_call_record(
        call_id="c1",
        tool_name="get_weather",
        args={"city": "Beijing"},
        result={"temp": 25},
    )
    signal = critic.critique(record)
    assert signal.severity == 0.1
    assert "succeeded" in signal.feedback_text


def test_critic_on_format_error() -> None:
    """format 错误产生 schema 类别反馈。"""
    critic = ToolCallCritic()
    record = make_call_record(
        call_id="c1",
        tool_name="get_date",
        args={"format": "wrong"},
        error="format must be YYYY-MM-DD",
    )
    signal = critic.critique(record)
    assert signal.severity > 0.5
    assert signal.extra_context["category"] == "schema"
    assert "schema" in signal.feedback_text.lower() or "description" in signal.feedback_text.lower()


def test_critic_on_permission_error() -> None:
    """permission 错误。"""
    critic = ToolCallCritic()
    record = make_call_record(
        call_id="c1",
        tool_name="admin_action",
        args={},
        error="unauthorized: missing token",
    )
    signal = critic.critique(record)
    assert signal.extra_context["category"] == "permission"


def test_critic_on_missing_error() -> None:
    """missing 错误。"""
    critic = ToolCallCritic()
    record = make_call_record(
        call_id="c1",
        tool_name="get_user",
        args={"id": 999},
        error="user not found",
    )
    signal = critic.critique(record)
    assert signal.extra_context["category"] == "missing"


def test_critic_on_general_error() -> None:
    """其他错误归为 general。"""
    critic = ToolCallCritic()
    record = make_call_record(
        call_id="c1",
        tool_name="x",
        args={},
        error="something weird happened",
    )
    signal = critic.critique(record)
    assert signal.extra_context["category"] == "general"


def test_critic_batch() -> None:
    """critique_batch 批量。"""
    critic = ToolCallCritic()
    records = [
        make_call_record(call_id="1", tool_name="a", args={}, result="ok"),
        make_call_record(call_id="2", tool_name="b", args={}, error="bad format"),
    ]
    signals = critic.critique_batch(records)
    assert len(signals) == 2
    assert signals[0].severity == 0.1
    assert signals[1].severity > 0.5


# ============================================================================
# ToolBPEngine
# ============================================================================


def test_engine_no_applicable_ops_returns_unchanged() -> None:
    """feedback 不匹配任何启发式时返回原 schema（version 不变）。"""
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="s1",
        name="get_date",
        schema_dict={
            "type": "object",
            "properties": {"format": {"type": "string"}},
        },
    )
    from dmsa.types import FeedbackSignal

    feedback = FeedbackSignal(
        feedback_id="f1",
        target_node_id="get_date",
        feedback_text="this is a random comment with no specific keywords",
        severity=0.5,
        source="USER",
    )
    result = engine.project(schema=schema, feedback=feedback)
    assert result.version == schema.version
    assert result.schema_dict == schema.schema_dict


def test_engine_format_error_updates_description() -> None:
    """format 错误 → UpdateLeafOp 改 description。"""
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="s1",
        name="get_date",
        schema_dict={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "a date string"},
            },
        },
    )
    from dmsa.toolbp.critic import ToolCallCritic

    record = make_call_record(
        call_id="c1",
        tool_name="get_date",
        args={"format": "wrong"},
        error="format must be YYYY-MM-DD",
    )
    signal = ToolCallCritic().critique(record)
    new_schema = engine.project(schema=schema, feedback=signal)
    assert new_schema.version == 2
    # description 应被增强
    new_props = new_schema.schema_dict["properties"]
    assert "format" in new_props
    assert new_props["format"]["description"] != "a date string"


def test_engine_required_error_toggles_required() -> None:
    """required 错误 → UpdateRequiredOp。"""
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="s1",
        name="get_user",
        schema_dict={
            "type": "object",
            "properties": {"user_id": {"type": "string", "description": "id"}},
        },
    )
    from dmsa.types import FeedbackSignal

    feedback = FeedbackSignal(
        feedback_id="f1",
        target_node_id="get_user",
        feedback_text="user_id field is required but missing field detected",
        severity=0.9,
        source="USER",
    )
    new_schema = engine.project(schema=schema, feedback=feedback)
    assert new_schema.version == 2
    assert "user_id" in new_schema.schema_dict["required"]


def test_engine_increments_version() -> None:
    """每次 project version+1。"""
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="s1",
        name="x",
        schema_dict={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    from dmsa.types import FeedbackSignal

    feedback = FeedbackSignal(
        feedback_id="f1",
        target_node_id="x",
        feedback_text="format error detected",
        severity=0.5,
        source="USER",
    )
    n1 = engine.project(schema=schema, feedback=feedback)
    assert n1.version == 2
    n2 = engine.project(schema=n1, feedback=feedback)
    assert n2.version == 3


def test_engine_with_existing_description_uses_heuristic() -> None:
    """已有 description 时 heuristics 推断目标。"""
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="s1",
        name="do_thing",
        schema_dict={
            "type": "object",
            "properties": {
                "alpha": {"type": "string", "description": ""},  # 空 description
                "beta": {"type": "string", "description": "very long detailed description here"},
            },
        },
    )
    from dmsa.types import FeedbackSignal

    feedback = FeedbackSignal(
        feedback_id="f1",
        target_node_id="do_thing",
        feedback_text="format error: type mismatch detected",
        severity=0.5,
        source="USER",
    )
    new_schema = engine.project(schema=schema, feedback=feedback)
    # 应该选 alpha（description 更短 = 更模糊）
    alpha_desc = new_schema.schema_dict["properties"]["alpha"]["description"]
    assert alpha_desc != ""  # 被增强了


# 抑制 unused import warnings
_ = (UnsupportedEditError, ToolBPError, ProjectionRollbackError, SchemaNode, EditOp)
