"""stdio MCP 的稠密混合检索工具实现."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.memory import CodingMemory


@dataclass(frozen=True)
class HybridArgs:
    """已校验的混合检索参数."""

    query: str
    top_k: int
    repo: str


def parse_hybrid(arguments: dict[str, Any]) -> HybridArgs:
    """校验混合检索参数; top_k 必须是 1 到 20 的真整数."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be nonempty text")
    top_k = arguments.get("top_k", 5)
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be an integer between 1 and 20")
    repo = arguments.get("repo", "")
    if not isinstance(repo, str):
        raise ValueError("repo must be text")
    return HybridArgs(query=query, top_k=top_k, repo=repo)


def hybrid_payload(
    memory: CodingMemory, scope: str, args: HybridArgs, rsi: Any = None
) -> dict[str, Any]:
    """返回稠密混合检索结果, 并给出每条证据的融合明细."""
    policy = None
    if rsi is not None:
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            policy = rsi.policy(store, scope).search
        finally:
            store.close()
    result = memory.search_hybrid(args.query, scope, args.top_k, policy=policy, repo=args.repo)
    return {"scope": scope, "repo": args.repo, **result.to_dict()}
