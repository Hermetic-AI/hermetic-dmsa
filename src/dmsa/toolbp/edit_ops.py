"""投影编辑指令（EditOp）。

支持的指令：

- :class:`UpdateLeafOp` — 修改节点的 description / type 字段
- :class:`UpdateRequiredOp` — 修改 required 标记

每条指令都有 :func:`validate_edit_op` 做 schema 一致性校验。
"""

from __future__ import annotations

from dataclasses import dataclass

from dmsa.errors import SchemaValidationError, UnsupportedEditError
from dmsa.toolbp.ast_projection import SchemaNode, find_node, update_node

__all__ = [
    "EditOp",
    "UpdateLeafOp",
    "UpdateRequiredOp",
    "apply_edit_op",
    "validate_edit_op",
]


@dataclass(frozen=True, slots=True)
class EditOp:
    """编辑指令基类（仅作类型标记）。"""

    field_path: tuple[str, ...]
    op_type: str = "EditOp"


@dataclass(frozen=True, slots=True)
class UpdateLeafOp(EditOp):
    """修改某个叶节点的字段（description / type）。

    Attributes:
        field_path: 目标节点路径（不含 ``"properties"`` 前缀，如 ``("format",)``）。
        new_description: 新 description（None 表示不修改）。
        new_type: 新 type（None 表示不修改）。
    """

    op_type: str = "UpdateLeafOp"
    new_description: str | None = None
    new_type: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateRequiredOp(EditOp):
    """修改某个 property 节点的 required 标记。

    Attributes:
        field_path: 目标节点路径。
        required: 新 required 值。
    """

    op_type: str = "UpdateRequiredOp"
    required: bool = True


def validate_edit_op(
    op: EditOp,
    root: SchemaNode,
) -> None:
    """校验编辑指令能作用在 ``root`` 上。

    Raises:
        UnsupportedEditError: op 类型不支持。
        SchemaValidationError: 目标节点不存在或字段不合法。
    """
    if not isinstance(op, EditOp):
        raise UnsupportedEditError(
            f"expected EditOp, got {type(op).__name__}",
            details={"type": type(op).__name__},
        )
    # 把短路径（"format"）转成完整路径（"properties.format"）
    full_path = _expand_path(root, op.field_path)
    target = find_node(root, full_path)
    if target is None:
        raise SchemaValidationError(
            f"target node not found at {op.field_path}",
            details={"path": list(op.field_path)},
        )
    if isinstance(op, UpdateLeafOp):
        if op.new_description is not None and not isinstance(op.new_description, str):
            raise SchemaValidationError(
                "new_description must be str",
                details={"type": type(op.new_description).__name__},
            )
        if op.new_type is not None and op.new_type not in (
            "object",
            "string",
            "number",
            "integer",
            "boolean",
            "array",
            "null",
        ):
            raise SchemaValidationError(
                f"new_type must be a valid JSON Schema type, got {op.new_type!r}",
                details={"new_type": op.new_type},
            )
    elif isinstance(op, UpdateRequiredOp):
        # target 必须是 object 的 property 节点（不是 root 本身）
        if not target.path or len(target.path) < 2:
            raise SchemaValidationError(
                "UpdateRequiredOp only applies to property nodes (not root)",
                details={"path": list(target.path)},
            )
        if target.path[0] != "properties":
            raise SchemaValidationError(
                "UpdateRequiredOp target must be under 'properties'",
                details={"path": list(target.path)},
            )


def apply_edit_op(
    op: EditOp,
    root: SchemaNode,
) -> SchemaNode:
    """应用编辑指令，返回新的 :class:`SchemaNode` 树（copy-on-write）。

    Raises:
        UnsupportedEditError / SchemaValidationError: 见 :func:`validate_edit_op`。
    """
    validate_edit_op(op, root)
    full_path = _expand_path(root, op.field_path)
    target = find_node(root, full_path)
    assert target is not None  # validate_edit_op 已检查
    if isinstance(op, UpdateLeafOp):
        new_node = SchemaNode(
            path=target.path,
            name=target.name,
            schema_type=op.new_type if op.new_type is not None else target.schema_type,
            description=op.new_description
            if op.new_description is not None
            else target.description,
            required=target.required,
            properties=target.properties,
            items=target.items,
        )
    elif isinstance(op, UpdateRequiredOp):
        new_node = SchemaNode(
            path=target.path,
            name=target.name,
            schema_type=target.schema_type,
            description=target.description,
            required=op.required,
            properties=target.properties,
            items=target.items,
        )
    else:
        raise UnsupportedEditError(
            f"unsupported op type: {type(op).__name__}",
            details={"op_type": type(op).__name__},
        )
    return update_node(root, new_node)


def _expand_path(root: SchemaNode, short: tuple[str, ...]) -> tuple[str, ...]:
    """把 ``("format",)`` 转成 ``("properties", "format")``（如果 root 是 object）。"""
    if root.schema_type != "object":
        return short
    if not short:
        return ()
    if short[0] in root.properties:
        return ("properties", *short)
    return short
