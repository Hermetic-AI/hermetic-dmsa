"""stdio MCP 的时序 as-of 检索工具实现."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.memory import AsOf, CodingMemory


@dataclass(frozen=True)
class TemporalArgs:
    """已校验的时序检索参数."""

    query: str
    top_k: int
    repo: str
    as_of: AsOf


def _coordinate(arguments: dict[str, Any], field: str) -> int | None:
    """读取并校验一个时序坐标; 未给出时返回 None."""
    value = arguments.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return int(value)


def parse_temporal(arguments: dict[str, Any]) -> TemporalArgs:
    """校验时序检索参数; 写入序号与写入时刻至少要给出一种."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be nonempty text")
    top_k = arguments.get("top_k", 5)
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be an integer between 1 and 20")
    repo = arguments.get("repo", "")
    if not isinstance(repo, str):
        raise ValueError("repo must be text")
    revision = _coordinate(arguments, "as_of_revision")
    timestamp = _coordinate(arguments, "as_of_timestamp")
    if revision is None and timestamp is None:
        raise ValueError("as_of_revision or as_of_timestamp is required")
    return TemporalArgs(
        query=query,
        top_k=top_k,
        repo=repo,
        as_of=AsOf(revision=revision, timestamp=timestamp),
    )


def temporal_payload(
    memory: CodingMemory, scope: str, args: TemporalArgs, rsi: Any = None
) -> dict[str, Any]:
    """返回时序检索结果, 并给出作用域当前的写入序号."""
    policy = None
    if rsi is not None:
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            policy = rsi.policy(store, scope).search
        finally:
            store.close()
    result = memory.search_as_of(
        args.query, scope, args.top_k, args.as_of, policy=policy, repo=args.repo
    )
    return {"scope": scope, "repo": args.repo, **result.to_dict()}
