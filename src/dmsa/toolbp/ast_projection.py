"""JSON Schema ↔ 树形结构互转（ast_projection）。

设计：把 JSON Schema 解析为 :class:`SchemaNode` 不可变树，编辑指令在树上操作。
支持子集：JSON Schema 草案 7 / 2020-12 的 ``properties`` / ``required`` / ``type`` /
``description`` 等常用字段。

依赖规则：只 import dmsa.errors。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from dmsa.errors import SchemaValidationError

__all__ = ["SchemaNode", "dump_schema", "find_node", "parse_schema", "update_node"]


@dataclass(frozen=True, slots=True)
class SchemaNode:
    """JSON Schema 树节点（不可变）。

    Attributes:
        path: 字段路径（如 ``["properties", "format"]``）。
        name: 当前节点名（如 ``"format"``）；根节点为 ``""``。
        schema_type: JSON Schema type（``"object"`` / ``"string"`` / ...）。
        description: 节点描述（可被反向传播修改）。
        required: 字段是否必填（仅 type=object 的 property 节点适用）。
        properties: 子节点（type=object 时）。
        items: 列表项节点（type=array 时）。
    """

    path: tuple[str, ...]
    name: str
    schema_type: str = "object"
    description: str = ""
    required: bool = False
    properties: dict[str, SchemaNode] = field(default_factory=dict)
    items: SchemaNode | None = None

    def to_dict(self) -> dict[str, Any]:
        """导出为 JSON Schema dict。"""
        result: dict[str, Any] = {"type": self.schema_type}
        if self.description:
            result["description"] = self.description
        if self.properties:
            result["properties"] = {
                name: child.to_dict() for name, child in self.properties.items()
            }
        if self.properties:
            # required 字段：所有标记为 required 的子节点
            req = [name for name, child in self.properties.items() if child.required]
            if req:
                result["required"] = req
        if self.items is not None:
            result["items"] = self.items.to_dict()
        return result


def parse_schema(schema: dict[str, Any], *, path: tuple[str, ...] = ()) -> SchemaNode:
    """从 JSON Schema dict 构造 :class:`SchemaNode` 树。"""
    name = path[-1] if path else ""
    schema_type = str(schema.get("type", "object"))
    description = str(schema.get("description", ""))
    children: dict[str, SchemaNode] = {}
    items_node: SchemaNode | None = None

    if schema_type == "object" and "properties" in schema:
        required_set = set(schema.get("required", []))
        for prop_name, prop_schema in schema["properties"].items():
            child = parse_schema(prop_schema, path=(*path, "properties", prop_name))
            # 标记 required
            if prop_name in required_set:
                child = SchemaNode(
                    path=child.path,
                    name=child.name,
                    schema_type=child.schema_type,
                    description=child.description,
                    required=True,
                    properties=child.properties,
                    items=child.items,
                )
            children[prop_name] = child

    if schema_type == "array" and "items" in schema:
        items_node = parse_schema(schema["items"], path=(*path, "items"))

    return SchemaNode(
        path=path,
        name=name,
        schema_type=schema_type,
        description=description,
        required=False,
        properties=children,
        items=items_node,
    )


def dump_schema(root: SchemaNode) -> dict[str, Any]:
    """把 :class:`SchemaNode` 树导出回 JSON Schema dict。"""
    return root.to_dict()


def find_node(root: SchemaNode, field_path: tuple[str, ...]) -> SchemaNode | None:
    """根据字段路径找节点。``field_path`` 是 ``("properties", "format", ...)`` 形式。

    每个 ``"properties"`` segment 都跳到对应子节点（object 的 properties dict）。
    """
    if not field_path:
        return root
    current: SchemaNode | None = root
    for segment in field_path:
        if current is None:
            return None
        if segment == "properties":
            continue  # skip — properties 是 dict key，下一个 segment 才是子节点名
        if segment in current.properties:
            current = current.properties[segment]
        elif current.items is not None and segment == "items":
            current = current.items
        else:
            return None
    return current


def update_node(root: SchemaNode, target: SchemaNode) -> SchemaNode:
    """返回一棵新树，把 ``target`` 节点替换到对应路径上（copy-on-write）。"""
    if not target.path:
        return target  # 替换根
    if not root.path and len(target.path) >= 2:
        # target 是 root.properties.X 形式
        key = target.path[1]
        new_props = dict(root.properties)
        new_props[key] = target
        return SchemaNode(
            path=root.path,
            name=root.name,
            schema_type=root.schema_type,
            description=root.description,
            required=root.required,
            properties=new_props,
            items=root.items,
        )
    raise SchemaValidationError(
        f"unsupported target path: {target.path}",
        details={"path": target.path},
    )


def iter_nodes(root: SchemaNode) -> Iterable[SchemaNode]:
    """遍历所有节点（DFS）。"""
    yield root
    for child in root.properties.values():
        yield from iter_nodes(child)
    if root.items is not None:
        yield from iter_nodes(root.items)
