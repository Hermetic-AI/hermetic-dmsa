"""stdio MCP 工具参数校验（从 coding_memory 拆出，保持单文件 ≤300 行）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.mcp_server.coding_tool_schemas import (
    FEEDBACK_KINDS,
    OUTCOMES,
    PROCEDURE_ACTIONS,
)
from dmsa.mcp_server.coding_tool_schemas_ext import (
    CONFLICT_ACTIONS,
    CONFLICT_RESOLUTIONS,
    CONFLICT_STATUSES,
)


@dataclass(frozen=True)
class SearchArgs:
    """已校验的检索参数."""

    query: str
    top_k: int
    task_id: str | None
    repo: str


@dataclass(frozen=True)
class RecordArgs:
    """已校验的经验写入参数."""

    request_id: str
    session_id: str
    content: str
    author: str
    repo: str


@dataclass(frozen=True)
class FeedbackArgs:
    """已校验的检索反馈参数."""

    trace_id: str
    outcome: str
    kind: str
    relevant_ids: tuple[str, ...]


@dataclass(frozen=True)
class GovernanceArgs:
    """已校验的记忆治理参数."""

    repo: str
    forget: bool
    floor: float
    min_age_days: int


@dataclass(frozen=True)
class ProcedureArgs:
    """已校验的程序性记忆参数."""

    action: str
    request_id: str
    session_id: str
    content: str
    query: str
    top_k: int
    repo: str


@dataclass(frozen=True)
class TaskResultArgs:
    """已校验的任务结果参数."""

    task_id: str
    trace_id: str
    outcome: str
    tests_passed: int
    files_changed: tuple[str, ...]
    tokens: int
    latency_ms: int
    detail: str


@dataclass(frozen=True)
class ConflictArgs:
    """已校验的写冲突仲裁参数."""

    action: str
    conflict_id: str
    resolution: str
    status: str
    limit: int
    repo: str


@dataclass(frozen=True)
class RecallArgs:
    """已校验的召回解释参数."""

    query: str
    top_k: int
    hops: int
    repo: str


def _text(value: Any, field: str) -> str:
    """要求字段是非空文本."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be nonempty text")
    return value


def _optional_text(value: Any, field: str) -> str:
    """要求字段是文本; 空字符串表示不启用该维度."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value


def _outcome(value: Any) -> str:
    """要求结果字段是 success 或 failure."""
    if value not in OUTCOMES:
        raise ValueError("outcome must be success or failure")
    return str(value)


def parse_search(arguments: dict[str, Any]) -> SearchArgs:
    """校验检索参数; top_k 必须是真整数而非布尔值."""
    top_k = arguments.get("top_k", 5)
    task_id = arguments.get("task_id")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be an integer between 1 and 20")
    if task_id is not None and not isinstance(task_id, str):
        raise ValueError("task_id must be text")
    return SearchArgs(
        query=_text(arguments.get("query"), "query"),
        top_k=top_k,
        task_id=task_id,
        repo=_optional_text(arguments.get("repo"), "repo"),
    )


def parse_record(arguments: dict[str, Any]) -> RecordArgs:
    """校验经验写入参数; author 是可选的作者标识."""
    return RecordArgs(
        request_id=_text(arguments.get("request_id"), "request_id"),
        session_id=_text(arguments.get("session_id"), "session_id"),
        content=_text(arguments.get("content"), "content"),
        author=_optional_text(arguments.get("author"), "author"),
        repo=_optional_text(arguments.get("repo"), "repo"),
    )


def parse_feedback(arguments: dict[str, Any]) -> FeedbackArgs:
    """校验检索反馈参数; kind 必须是已登记的根因类别."""
    kind = arguments.get("kind", "unknown")
    relevant = arguments.get("relevant_ids", [])
    if not isinstance(kind, str) or kind not in FEEDBACK_KINDS:
        raise ValueError("kind must be a registered feedback kind")
    if not isinstance(relevant, list) or any(not isinstance(item, str) for item in relevant):
        raise ValueError("relevant_ids must be a list of text")
    return FeedbackArgs(
        trace_id=_text(arguments.get("trace_id"), "trace_id"),
        outcome=_outcome(arguments.get("outcome")),
        kind=kind,
        relevant_ids=tuple(relevant),
    )


def parse_governance(arguments: dict[str, Any]) -> GovernanceArgs:
    """校验记忆治理参数; 阈值必须是有限数值, 天数必须是非负整数."""
    floor = arguments.get("floor", 0.1)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 <= floor <= 1:
        raise ValueError("floor must be a number between 0 and 1")
    min_age_days = arguments.get("min_age_days", 30)
    if type(min_age_days) is not int or min_age_days < 0:
        raise ValueError("min_age_days must be a nonnegative integer")
    forget = arguments.get("forget", False)
    if not isinstance(forget, bool):
        raise ValueError("forget must be a boolean")
    return GovernanceArgs(
        repo=_optional_text(arguments.get("repo"), "repo"),
        forget=forget,
        floor=float(floor),
        min_age_days=min_age_days,
    )


def parse_procedure(arguments: dict[str, Any]) -> ProcedureArgs:
    """校验程序性记忆参数; action 必须是 record 或 search."""
    action = arguments.get("action", "search")
    if action not in PROCEDURE_ACTIONS:
        raise ValueError("action must be record or search")
    top_k = arguments.get("top_k", 3)
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise ValueError("top_k must be an integer between 1 and 10")
    return ProcedureArgs(
        action=action,
        request_id=_optional_text(arguments.get("request_id"), "request_id"),
        session_id=_optional_text(arguments.get("session_id"), "session_id"),
        content=_optional_text(arguments.get("content"), "content"),
        query=_optional_text(arguments.get("query"), "query"),
        top_k=top_k,
        repo=_optional_text(arguments.get("repo"), "repo"),
    )


def parse_task_result(arguments: dict[str, Any]) -> TaskResultArgs:
    """校验任务结果参数; 计数字段必须是非负整数."""
    counters = {name: arguments.get(name, 0) for name in ("tests_passed", "tokens", "latency_ms")}
    for name, value in counters.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    changed = arguments.get("files_changed", [])
    detail = arguments.get("detail", "")
    if not isinstance(changed, list) or any(not isinstance(item, str) for item in changed):
        raise ValueError("files_changed must be a list of paths")
    if not isinstance(detail, str):
        raise ValueError("detail must be text")
    return TaskResultArgs(
        task_id=_text(arguments.get("task_id"), "task_id"),
        trace_id=_text(arguments.get("trace_id"), "trace_id"),
        outcome=_outcome(arguments.get("outcome")),
        tests_passed=int(counters["tests_passed"]),
        files_changed=tuple(changed),
        tokens=int(counters["tokens"]),
        latency_ms=int(counters["latency_ms"]),
        detail=detail,
    )


def parse_conflict(arguments: dict[str, Any]) -> ConflictArgs:
    """校验写冲突仲裁参数; action 必须是 list 或 resolve."""
    action = arguments.get("action", "list")
    if action not in CONFLICT_ACTIONS:
        raise ValueError("action must be list or resolve")
    resolution = arguments.get("resolution", "")
    if resolution and resolution not in CONFLICT_RESOLUTIONS:
        raise ValueError("resolution must be a registered conflict resolution")
    status = arguments.get("status", "")
    if status and status not in CONFLICT_STATUSES:
        raise ValueError("status must be a registered conflict status")
    limit = arguments.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    conflict_id = _optional_text(arguments.get("conflict_id"), "conflict_id")
    if action == "resolve" and not conflict_id:
        raise ValueError("conflict_id is required to resolve a conflict")
    if action == "resolve" and not resolution:
        raise ValueError("resolution is required to resolve a conflict")
    return ConflictArgs(
        action=action,
        conflict_id=conflict_id,
        resolution=str(resolution),
        status=str(status),
        limit=limit,
        repo=_optional_text(arguments.get("repo"), "repo"),
    )


def parse_recall(arguments: dict[str, Any]) -> RecallArgs:
    """校验召回解释参数; hops 必须是 1 到 3 的真整数."""
    top_k = arguments.get("top_k", 5)
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be an integer between 1 and 20")
    hops = arguments.get("hops", 1)
    if type(hops) is not int or not 1 <= hops <= 3:
        raise ValueError("hops must be an integer between 1 and 3")
    return RecallArgs(
        query=_text(arguments.get("query"), "query"),
        top_k=top_k,
        hops=hops,
        repo=_optional_text(arguments.get("repo"), "repo"),
    )
