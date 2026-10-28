"""stdio MCP 的状态、治理与冲突仲裁工具实现（从 coding_memory 拆出，保持单文件 ≤300 行）。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from dmsa.gradient.effectiveness import measure_effectiveness
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.mcp_server.coding_tools import ConflictArgs, RecallArgs
from dmsa.memory.coding import CodingMemory


def status_payload(memory: CodingMemory, scope: str, rsi: Any, path: str | Path) -> dict[str, Any]:
    """报告后端、数据库、scope 以及 RSI 与当前活动策略是否真的生效。"""
    location = Path(path)
    payload: dict[str, Any] = {
        "backend": "CodingMemory",
        "database": str(location),
        "scope": scope,
        "available": location.exists() or str(location) == ":memory:",
    }
    if rsi is None:
        payload["rsi"] = {"enabled": False}
        return payload
    store = RetrievalFeedbackStore(memory.store.path)
    try:
        active = asdict(rsi.policy(store, scope).search)
    finally:
        store.close()
    payload["rsi"] = {
        "enabled": True,
        "models": rsi.models,
        "active_policy": active,
    }
    return payload


def governance_report(
    memory: CodingMemory,
    scope: str,
    *,
    repo: str,
    forget: bool,
    floor: float,
    min_age_days: int,
) -> dict[str, Any]:
    """返回 scope 内条目的信任报告, 可选执行遗忘淘汰。"""
    entries = memory.governance.trust_report(scope, repo=repo)
    payload: dict[str, Any] = {
        "scope": scope,
        "repo": repo,
        "count": len(entries),
        "entries": entries,
    }
    if forget:
        payload["evicted"] = list(
            memory.governance.forget(scope, repo=repo, floor=floor, min_age_days=min_age_days)
        )
    return payload


def effectiveness_payload(memory: CodingMemory, scope: str) -> dict[str, Any]:
    """度量记忆的实际有效性; 没有真实样本时如实留空."""
    report = measure_effectiveness(memory.store.path, scope)
    payload = report.to_dict()
    payload["samples"] = [
        {
            "task_id": item.task_id,
            "outcome": item.outcome,
            "trace_id": item.trace_id,
            "group": item.group,
            "confirmed_delivered": item.confirmed_delivered,
        }
        for item in report.samples
    ]
    return payload


def conflicts_payload(memory: CodingMemory, scope: str, args: ConflictArgs) -> dict[str, Any]:
    """列出待仲裁的跨作者写冲突, 或按结论仲裁其中一条."""
    if args.action == "list":
        found = memory.conflicts(scope, args.repo, status=args.status, limit=args.limit)
        return {
            "scope": scope,
            "repo": args.repo,
            "conflicts": [item.to_dict() for item in found],
        }
    resolution = memory.resolve_conflict(scope, args.conflict_id, args.resolution, args.repo)
    return {"scope": scope, "repo": args.repo, **resolution.to_dict()}


def recall_payload(
    memory: CodingMemory, scope: str, args: RecallArgs, rsi: Any = None
) -> dict[str, Any]:
    """返回带召回说明的检索结果; 有 RSI 时采用当前活动策略."""
    policy = None
    if rsi is not None:
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            policy = rsi.policy(store, scope).search
        finally:
            store.close()
    explanation = memory.search_explained(
        args.query, scope, args.top_k, policy=policy, repo=args.repo, hops=args.hops
    )
    return {"scope": scope, "repo": args.repo, **explanation.to_dict()}
