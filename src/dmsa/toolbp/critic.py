"""工具调用反馈生成器（ToolCallCritic）。

输入 :class:`ToolCallRecord`，输出 :class:`FeedbackSignal`。
零依赖：基于关键词 / 错误码启发式。
"""

from __future__ import annotations

import re
from typing import Mapping

from dmsa.types import FeedbackSignal, ToolCallRecord

__all__ = ["ToolCallCritic"]


class ToolCallCritic:
    """从 :class:`ToolCallRecord` 生成 :class:`FeedbackSignal`。

    启发式：

    - 错误含 ``format`` / ``schema`` / ``type`` / ``required`` → 提示描述 schema 不足
    - 错误含 ``permission`` / ``unauthorized`` → 提示权限
    - 错误含 ``not found`` / ``missing`` → 提示资源不存在
    - 其他错误 → 通用提示
    """

    __slots__ = ("_format_keywords", "_permission_keywords", "_missing_keywords", "_id_gen")

    def __init__(self) -> None:
        self._format_keywords = (
            "format", "schema", "type", "required", "json", "parse", "validation",
            "格式", "类型", "必填", "解析", "校验",
        )
        self._permission_keywords = (
            "permission", "unauthorized", "forbidden", "auth", "token",
            "权限", "授权", "令牌",
        )
        self._missing_keywords = (
            "not found", "missing", "404", "does not exist", "no such",
            "找不到", "不存在", "缺失",
        )

    def critique(self, record: ToolCallRecord) -> FeedbackSignal:
        """从 :class:`ToolCallRecord` 生成 :class:`FeedbackSignal`。"""
        if record.error is None:
            return FeedbackSignal(
                feedback_id=f"critic-{record.call_id}",
                target_node_id=record.tool_name,
                feedback_text=f"tool {record.tool_name!r} succeeded",
                severity=0.1,
                source="HEURISTIC",
                extra_context={"call_id": record.call_id, "result_type": type(record.result).__name__},
            )

        error_lower = record.error.lower()
        if any(kw in error_lower for kw in self._format_keywords):
            category = "schema"
            severity = 0.9
            suggestion = (
                f"tool {record.tool_name!r} failed due to format/schema error: "
                f"{record.error}. The tool's description or required field may be ambiguous; "
                f"consider updating the JSON Schema description to clarify the expected format."
            )
        elif any(kw in error_lower for kw in self._permission_keywords):
            category = "permission"
            severity = 0.7
            suggestion = (
                f"tool {record.tool_name!r} failed with permission error: {record.error}. "
                f"Check authentication credentials."
            )
        elif any(kw in error_lower for kw in self._missing_keywords):
            category = "missing"
            severity = 0.5
            suggestion = (
                f"tool {record.tool_name!r} failed: {record.error}. "
                f"Resource may not exist; check arguments."
            )
        else:
            category = "general"
            severity = 0.6
            suggestion = (
                f"tool {record.tool_name!r} failed: {record.error}. "
                f"Inspect arguments and tool documentation."
            )

        return FeedbackSignal(
            feedback_id=f"critic-{record.call_id}",
            target_node_id=record.tool_name,
            feedback_text=suggestion,
            severity=severity,
            source="HEURISTIC",
            extra_context={
                "call_id": record.call_id,
                "category": category,
                "error": record.error,
                "args": dict(record.args),
            },
        )

    def critique_batch(self, records: list[ToolCallRecord]) -> list[FeedbackSignal]:
        """批量 critique。"""
        return [self.critique(r) for r in records]


# Re-export
_ = re
