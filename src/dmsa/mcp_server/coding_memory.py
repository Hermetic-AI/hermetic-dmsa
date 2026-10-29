"""将 CodingMemory 通过 stdio MCP 暴露给 Codex。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.mcp_server.coding_consolidation import (
    ConsolidateArgs,
    TransferArgs,
    consolidate_payload,
    parse_consolidate,
    parse_transfer,
    transfer_payload,
)
from dmsa.mcp_server.coding_evidence import (
    procedure_payload,
    record_payload,
    search_payload,
)
from dmsa.mcp_server.coding_governance import (
    conflicts_payload,
    effectiveness_payload,
    governance_report,
    recall_payload,
    status_payload,
)
from dmsa.mcp_server.coding_hybrid import HybridArgs, hybrid_payload, parse_hybrid
from dmsa.mcp_server.coding_temporal import TemporalArgs, parse_temporal, temporal_payload
from dmsa.mcp_server.coding_tool_schemas import TOOLS
from dmsa.mcp_server.coding_tools import (
    ConflictArgs,
    FeedbackArgs,
    ProcedureArgs,
    RecallArgs,
    RecordArgs,
    SearchArgs,
    TaskResultArgs,
    parse_conflict,
    parse_feedback,
    parse_governance,
    parse_procedure,
    parse_recall,
    parse_record,
    parse_search,
    parse_task_result,
)
from dmsa.mcp_server.protocol import (
    JsonRpcErrorCode,
    JsonRpcRequest,
    make_error_response,
    make_response,
)
from dmsa.memory import CodingMemory
from dmsa.observability import get_logger

_logger = get_logger("dmsa.mcp_server.coding_memory")


class CodingMemoryMCPServer:
    """使用 DMSA CodingMemory 实例响应 MCP 工具请求。"""

    def __init__(
        self,
        memory: CodingMemory,
        scope: str,
        path: Path,
        rsi: IntegratedRSI | None = None,
    ) -> None:
        """保存已打开的记忆库、只读连接信息与可选 RSI 协调器。"""
        self.memory = memory
        self.scope = scope
        self.path = path
        self.rsi = rsi

    async def handle(self, request: JsonRpcRequest) -> dict[str, Any] | None:
        """分发 MCP 请求；通知消息不返回 JSON-RPC 响应。"""
        if request.method == "initialize":
            return make_response(
                {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "dmsa-coding-memory", "version": "0.1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
                request.id,
            )
        if request.method == "notifications/initialized":
            return None
        if request.method == "ping":
            return make_response({}, request.id)
        if request.method == "tools/list":
            return make_response({"tools": TOOLS}, request.id)
        if request.method == "tools/call":
            return self._call(request)
        return make_error_response(
            JsonRpcErrorCode.METHOD_NOT_FOUND.value,
            f"method not found: {request.method!r}",
            request_id=request.id,
        )

    def _call(self, request: JsonRpcRequest) -> dict[str, Any]:
        """校验参数并调用 CodingMemory 公共 API。"""
        params = request.params
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return self._tool_error(request, "arguments must be an object")
        try:
            result: Any = self._dispatch(name, arguments)
            content_result = {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            }
            _logger.debug(
                "coding memory tool handled",
                tool=str(name),
                scope=self.scope,
                payload_bytes=len(content_result["content"][0]["text"].encode("utf-8")),
            )
            return make_response(content_result, request.id)
        except Exception as exc:
            _logger.error(
                "coding memory MCP operation failed",
                tool=str(name),
                scope=self.scope,
                error_type=type(exc).__name__,
            )
            return self._tool_error(request, "coding memory operation failed")

    def _dispatch(self, name: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        """按工具名分派到对应实现。"""
        if name == "dmsa_coding_memory_status":
            return self._status()
        if name == "dmsa_coding_memory_search":
            return self._search(parse_search(arguments))
        if name == "dmsa_coding_memory_record":
            return self._record(parse_record(arguments))
        if name == "dmsa_coding_memory_feedback":
            return self._feedback(parse_feedback(arguments))
        if name == "dmsa_coding_memory_task_result":
            return self._task_result(parse_task_result(arguments))
        if name == "dmsa_coding_memory_governance":
            return self._governance(parse_governance(arguments))
        if name == "dmsa_coding_memory_effectiveness":
            return self._effectiveness()
        if name == "dmsa_coding_memory_procedure":
            return self._procedure(parse_procedure(arguments))
        if name == "dmsa_coding_memory_conflicts":
            return self._conflicts(parse_conflict(arguments))
        if name == "dmsa_coding_memory_recall":
            return self._recall(parse_recall(arguments))
        if name == "dmsa_coding_memory_consolidate":
            return self._consolidate(parse_consolidate(arguments))
        if name == "dmsa_coding_memory_transfer":
            return self._transfer(parse_transfer(arguments))
        if name == "dmsa_coding_memory_hybrid_search":
            return self._hybrid(parse_hybrid(arguments))
        if name == "dmsa_coding_memory_search_as_of":
            return self._temporal(parse_temporal(arguments))
        raise ValueError(f"unknown tool: {name!r}")

    def _status(self) -> dict[str, Any]:
        """报告后端、数据库、scope 以及 RSI 与当前活动策略是否真的生效。"""
        return status_payload(self.memory, self.scope, self.rsi, str(self.path))

    def _governance(self, args: Any) -> dict[str, Any]:
        """报告记忆条目的信任度, 可选执行遗忘淘汰。"""
        return governance_report(
            self.memory,
            self.scope,
            repo=args.repo,
            forget=args.forget,
            floor=args.floor,
            min_age_days=args.min_age_days,
        )

    def _effectiveness(self) -> dict[str, Any]:
        """度量记忆的实际有效性; 没有真实样本时如实留空."""
        return effectiveness_payload(self.memory, self.scope)

    def _procedure(self, args: ProcedureArgs) -> dict[str, Any]:
        """沉淀或匹配程序性记忆; 没有显式步骤时不落盘并如实回报."""
        return procedure_payload(self.memory, self.scope, args)

    def _conflicts(self, args: ConflictArgs) -> dict[str, Any]:
        """列出待仲裁的跨作者写冲突, 或按结论仲裁其中一条."""
        return conflicts_payload(self.memory, self.scope, args)

    def _recall(self, args: RecallArgs) -> dict[str, Any]:
        """返回带召回说明的检索结果, 可选多跳迭代扩展."""
        return recall_payload(self.memory, self.scope, args, self.rsi)

    def _consolidate(self, args: ConsolidateArgs) -> dict[str, Any]:
        """合并同一作用域内重复出现的经验, 返回合并报告."""
        return consolidate_payload(self.memory, self.scope, args)

    def _transfer(self, args: TransferArgs) -> dict[str, Any]:
        """把可复用的通用规则迁移到另一个仓库, 返回迁移报告."""
        return transfer_payload(self.memory, self.scope, args)

    def _hybrid(self, args: HybridArgs) -> dict[str, Any]:
        """执行稠密混合检索, 返回融合明细与重排增量."""
        return hybrid_payload(self.memory, self.scope, args, self.rsi)

    def _temporal(self, args: TemporalArgs) -> dict[str, Any]:
        """按时序坐标检索历史证据, 返回当时可见的内容与当前写入序号."""
        return temporal_payload(self.memory, self.scope, args, self.rsi)

    def _search(self, args: SearchArgs) -> dict[str, Any]:
        """检索证据; 有 RSI 时走活动策略并返回可反馈的轨迹 ID。"""
        return search_payload(self.memory, self.scope, args, self.rsi)

    def _record(self, args: RecordArgs) -> dict[str, Any]:
        """写入一条已验证的工程经验, 并回报写入门控的判定。"""
        return record_payload(self.memory, self.scope, args)

    def _feedback(self, args: FeedbackArgs) -> dict[str, Any]:
        """保存显式检索反馈, 并触发一次受门禁保护的策略进化."""
        if self.rsi is None:
            raise ValueError("feedback requires persistent memory")
        store = RetrievalFeedbackStore(self.memory.store.path)
        try:
            changed, attribution = store.feedback_with_attribution(
                self.scope, args.trace_id, args.outcome, args.kind, list(args.relevant_ids)
            )
            store.event(
                self.scope,
                "feedback_saved",
                {
                    "trace_id": args.trace_id,
                    "outcome": args.outcome,
                    "kind": args.kind,
                },
            )
        finally:
            store.close()
        return {
            "scope": self.scope,
            "stored": True,
            "duplicate": not changed,
            "attribution": attribution,
            "evolution": self.rsi.evolve(self.scope)
            if changed
            else {"status": "duplicate_feedback"},
        }

    def _task_result(self, args: TaskResultArgs) -> dict[str, Any]:
        """保存真实任务结果, 与检索反馈分开存放."""
        if self.rsi is None:
            raise ValueError("task result requires persistent memory")
        return self.rsi.record_task_result(
            self.scope,
            args.task_id,
            args.trace_id,
            args.outcome,
            tests_passed=args.tests_passed,
            files_changed=args.files_changed,
            tokens=args.tokens,
            latency_ms=args.latency_ms,
            detail=args.detail,
        )

    @staticmethod
    def _tool_error(request: JsonRpcRequest, message: str) -> dict[str, Any]:
        """返回 MCP 工具级错误。"""
        result = {"isError": True, "content": [{"type": "text", "text": message}]}
        return make_response(result, request.id)

    async def run_stdio(self, stdin: TextIO, stdout: TextIO) -> None:
        """跨 Windows 与 POSIX 读取行式 stdio JSON-RPC。"""
        from dmsa.mcp_server.coding_stdio import serve

        await serve(self, stdin, stdout)


def main() -> int:
    """以 MCP stdio 模式运行项目级 CodingMemory。"""
    from dmsa.mcp_server.coding_stdio import run_server

    return run_server()


if __name__ == "__main__":
    raise SystemExit(main())
