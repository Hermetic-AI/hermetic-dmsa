"""stdio MCP 的证据读写工具实现（从 coding_memory 拆出，保持单文件 ≤300 行）。"""

from __future__ import annotations

from typing import Any

from dmsa.gradient.integrated import IntegratedRSI
from dmsa.mcp_server.coding_tools import ProcedureArgs, RecordArgs, SearchArgs
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def expose(memory: CodingMemory, scope: str, hits: list[Any], repo: str) -> None:
    """记录命中条目的曝光，供信任模型积累证据。"""
    ids = [str(item["id"]) for item in hits if isinstance(item, dict) and item.get("id")]
    if ids:
        memory.governance.record_exposure(scope, ids, repo=repo)


def search_payload(
    memory: CodingMemory,
    scope: str,
    args: SearchArgs,
    rsi: IntegratedRSI | None = None,
) -> dict[str, Any]:
    """检索证据；有 RSI 时走活动策略并返回可反馈的轨迹 ID。"""
    if rsi is not None:
        payload = rsi.search(
            args.query,
            scope,
            args.top_k,
            task_id=args.task_id,
            dense=False,
            repo=args.repo,
        )
        hits = list(payload["data"])
        expose(memory, scope, hits, args.repo)
        return {
            "scope": scope,
            "repo": args.repo,
            "trace_id": payload["trace_id"],
            "hits": hits,
        }
    found = memory.search(
        args.query, scope, args.top_k, SearchPolicy(dense_weight=0.0), repo=args.repo
    )
    hits = [{"id": hit.id, "content": hit.content, "score": hit.score} for hit in found]
    expose(memory, scope, hits, args.repo)
    return {
        "scope": scope,
        "repo": args.repo,
        "hits": hits,
    }


def record_payload(memory: CodingMemory, scope: str, args: RecordArgs) -> dict[str, Any]:
    """写入一条已验证的工程经验，并回报写入门控的判定。"""
    result = memory.add_guarded(
        args.request_id,
        scope,
        args.session_id,
        [SourceMessage("assistant", args.content)],
        repo=args.repo,
        author=args.author,
    )
    return {
        "scope": scope,
        "repo": args.repo,
        "author": args.author,
        "revision": result.revision,
        "stored": result.decision.admitted,
        "admission": result.decision.to_dict(),
    }


def procedure_payload(memory: CodingMemory, scope: str, args: ProcedureArgs) -> dict[str, Any]:
    """沉淀或匹配程序性记忆；没有显式步骤时不落盘并如实回报。"""
    if args.action == "record":
        procedure = memory.add_procedure(
            args.request_id,
            scope,
            args.session_id,
            [SourceMessage("assistant", args.content)],
            repo=args.repo,
        )
        return {
            "scope": scope,
            "repo": args.repo,
            "stored": procedure is not None,
            "procedure": procedure.to_dict() if procedure is not None else None,
        }
    matches = memory.search_procedures(args.query, scope, args.top_k, repo=args.repo)
    return {
        "scope": scope,
        "repo": args.repo,
        "procedures": [item.to_dict() for item in matches],
    }
