"""ToolBPEngine — 编排 Tool-Use 反向传播。

主入口 :class:`ToolBPEngine`：

1. 接 :class:`FeedbackSignal`（针对某个 tool_name）
2. 找到对应的 :class:`ToolSchemaNode`
3. 把 feedback 转换为 :class:`EditOp`（UpdateLeafOp / UpdateRequiredOp）
4. 应用 edit op 到 schema
5. 校验更新后 schema 仍合法
6. 返回新 :class:`ToolSchemaNode`（含递增 version）

依赖规则：import dmsa.errors + dmsa.types + 同包模块（ast_projection / edit_ops / critic）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from dmsa.errors import (
    ProjectionRollbackError,
    SchemaValidationError,
    ToolBPError,
)
from dmsa.observability import get_logger, get_registry, start_span
from dmsa.toolbp.ast_projection import (
    SchemaNode,
    dump_schema,
    parse_schema,
)
from dmsa.toolbp.critic import ToolCallCritic
from dmsa.toolbp.edit_ops import (
    EditOp,
    UpdateLeafOp,
    UpdateRequiredOp,
    apply_edit_op,
    validate_edit_op,
)
from dmsa.types import FeedbackSignal, ToolSchemaNode

__all__ = ["ToolBPEngine"]


_logger = get_logger("dmsa.toolbp.projector")
_metrics = get_registry()


class ToolBPEngine:
    """Tool-Use 反向传播引擎。

    Args:
        critic: 反馈生成器（默认 :class:`ToolCallCritic`）。
    """

    def __init__(self, *, critic: ToolCallCritic | None = None) -> None:
        """初始化反向传播引擎, 缺省使用内置工具调用批评器。"""
        self._critic = critic or ToolCallCritic()

    def project(
        self,
        *,
        schema: ToolSchemaNode,
        feedback: FeedbackSignal,
    ) -> ToolSchemaNode:
        """根据 feedback 反向传播到 schema，返回新 :class:`ToolSchemaNode`（含 version+1）。

        Args:
            schema: 原始 tool schema。
            feedback: 反馈信号（feedback_text 描述问题）。

        Returns:
            新 :class:`ToolSchemaNode`（schema_dict 已更新，version +1）。

        Raises:
            SchemaValidationError: 反馈无法映射到合法 edit op。
            ProjectionRollbackError: 投影导致 schema 不合法，自动回滚。
        """
        with start_span(
            "dmsa.toolbp.project",
            tool_name=schema.name,
            feedback_id=feedback.feedback_id,
        ) as span:
            try:
                # 1. 解析 schema → tree
                root = parse_schema(dict(schema.schema_dict))

                # 2. feedback → edit ops
                ops = self._feedback_to_ops(schema.name, feedback, root)
                if not ops:
                    _logger.info(
                        "no applicable edit ops",
                        tool_name=schema.name,
                        feedback_id=feedback.feedback_id,
                    )
                    return schema

                # 3. 应用 ops（copy-on-write）；失败自动回滚
                new_root = root
                applied_ops: list[EditOp] = []
                for op in ops:
                    try:
                        validate_edit_op(op, new_root)
                    except SchemaValidationError as exc:
                        _logger.warning(
                            "skipping invalid op",
                            op_type=op.op_type,
                            path=list(op.field_path),
                            error=str(exc),
                        )
                        continue
                    new_root = apply_edit_op(op, new_root)
                    applied_ops.append(op)

                if not applied_ops:
                    return schema

                # 4. 校验更新后 schema
                new_schema_dict = dump_schema(new_root)
                if not self._is_schema_well_formed(new_schema_dict):
                    raise ProjectionRollbackError(
                        f"projected schema is malformed for {schema.name!r}",
                        details={"tool_name": schema.name, "schema": new_schema_dict},
                    )

                # 5. 返回新 ToolSchemaNode
                new_version = schema.version + 1
                new_node = ToolSchemaNode(
                    schema_id=schema.schema_id,
                    name=schema.name,
                    schema_dict=new_schema_dict,
                    version=new_version,
                    requires_grad=schema.requires_grad,
                    grad_learning_rate=schema.grad_learning_rate,
                )
                _metrics.counter("dmsa.toolbp.project").inc()
                _metrics.count_histogram("dmsa.toolbp.ops_applied").observe(len(applied_ops))
                span.set_attribute("version", new_version)
                span.set_attribute("ops_applied", len(applied_ops))
                _logger.info(
                    "tool schema updated",
                    tool_name=schema.name,
                    old_version=schema.version,
                    new_version=new_version,
                    ops_count=len(applied_ops),
                )
                return new_node
            except ProjectionRollbackError:
                raise
            except SchemaValidationError:
                raise
            except Exception as exc:
                raise ToolBPError(
                    f"project failed for {schema.name!r}: {exc}",
                    details={"tool_name": schema.name, "error": str(exc)},
                ) from exc

    def _feedback_to_ops(
        self,
        tool_name: str,
        feedback: FeedbackSignal,
        root: SchemaNode,
    ) -> list[EditOp]:
        """把 feedback 转换为 edit ops。

        启发式：
        - 关键词 "format" / "schema" / "类型" / "格式" → 找对应 property，更新 description
        - 关键词 "required" / "必填" → UpdateRequiredOp
        - 关键词 "should be more specific" / "明确" → 找 description 最短的 property 更新
        """
        ops: list[EditOp] = []
        text_lower = feedback.feedback_text.lower()

        # 启发式 1: format / schema 错误 → 找相关 property
        if any(
            kw in text_lower for kw in ("format", "schema", "类型", "格式", "parse", "validation")
        ):
            target = self._infer_target_property(root, feedback.feedback_text)
            if target is not None:
                ops.append(
                    UpdateLeafOp(
                        field_path=target,
                        new_description=self._enhanced_description(
                            target[-1], feedback.feedback_text
                        ),
                    )
                )

        # 启发式 2: required 关键字
        if any(kw in text_lower for kw in ("required", "必填", "missing field")):
            target = self._infer_target_property(root, feedback.feedback_text)
            if target is not None:
                ops.append(UpdateRequiredOp(field_path=target, required=True))

        return ops

    def _infer_target_property(
        self,
        root: SchemaNode,
        feedback_text: str,
    ) -> tuple[str, ...] | None:
        """从 feedback 文本推断目标 property 名。"""
        # 策略 1: 文本里直接出现 property 名（从 root.properties 找匹配）
        for prop_name in root.properties:
            if re.search(rf"\b{re.escape(prop_name)}\b", feedback_text, re.IGNORECASE):
                return (prop_name,)
        # 策略 2: 找 description 最短的 property（最可能是模糊的）
        if root.properties:
            shortest = min(
                root.properties.items(),
                key=lambda kv: len(kv[1].description),
            )
            return (shortest[0],)
        return None

    def _enhanced_description(self, prop_name: str, feedback_text: str) -> str:
        """根据 feedback 生成增强的 description。"""
        base = f"Field {prop_name!r}: "
        if "format" in feedback_text.lower() or "格式" in feedback_text:
            return base + "expected format must be specified in the call arguments."
        if "required" in feedback_text.lower() or "必填" in feedback_text:
            return base + "this field is required and must be provided."
        if "type" in feedback_text.lower() or "类型" in feedback_text:
            return base + "value must match the declared JSON Schema type."
        return base + feedback_text[:200]

    def _is_schema_well_formed(self, schema_dict: Mapping[str, object]) -> bool:
        """基本 sanity check：必须含 ``type`` 字段。"""
        return "type" in schema_dict and isinstance(schema_dict["type"], str)
