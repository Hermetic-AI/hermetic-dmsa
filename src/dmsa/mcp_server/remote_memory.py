"""把 CodingMemory MCP 工具桥接到统一 HTTP 记忆与 RSI 服务。"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import JsonRpcRequest, make_response


class RemoteMemoryMCPServer(CodingMemoryMCPServer):
    """复用 stdio 协议，但全部记忆操作进入同一 HTTP 后端。"""

    def __init__(self, url: str, key: str, scope: str) -> None:
        """绑定私有 Compose 网络或本机服务及默认项目 scope。"""
        self.scope = scope
        self.client = httpx.Client(
            trust_env=False, base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=180
        )

    def _call(self, request: JsonRpcRequest) -> dict[str, Any]:
        """将 MCP 工具参数转换成受认证 HTTP 调用。"""
        name, args = request.params.get("name"), request.params.get("arguments", {})
        if not isinstance(args, dict):
            return self._tool_error(request, "arguments must be an object")
        try:
            if name == "dmsa_coding_memory_status":
                response = self.client.get("/health")
            elif name == "dmsa_coding_memory_search":
                response = self.client.post(
                    "/search",
                    json={
                        "query": args.get("query"),
                        "top_k": args.get("top_k", 5),
                        "user_id": self.scope,
                        "task_id": args.get("task_id"),
                        "repo": args.get("repo", ""),
                    },
                )
            elif name == "dmsa_coding_memory_record":
                response = self.client.post(
                    "/add",
                    json={
                        "request_id": args.get("request_id"),
                        "session_id": args.get("session_id"),
                        "user_id": self.scope,
                        "repo": args.get("repo", ""),
                        "messages": [{"role": "assistant", "content": args.get("content")}],
                    },
                )
            elif name == "dmsa_coding_memory_feedback":
                response = self.client.post("/feedback", json={**args, "user_id": self.scope})
            elif name == "dmsa_coding_memory_task_result":
                response = self.client.post("/task_result", json={**args, "user_id": self.scope})
            else:
                return self._tool_error(request, "unknown tool")
            response.raise_for_status()
            return make_response(
                {
                    "content": [
                        {"type": "text", "text": json.dumps(response.json(), ensure_ascii=False)}
                    ]
                },
                request.id,
            )
        except (httpx.HTTPError, ValueError):
            return self._tool_error(request, "unified DMSA request failed")

    def close(self) -> None:
        """释放 HTTP 客户端。"""
        self.client.close()


def configured_remote(scope: str) -> RemoteMemoryMCPServer | None:
    """存在远端配置时切换为统一 HTTP 记忆与 RSI。"""
    url = os.environ.get("DMSA_CODING_MEMORY_URL", "").strip()
    key = os.environ.get("DMSA_MEMORY_API_KEY", "")
    env_file = os.environ.get("DMSA_ENV_FILE", "")
    if env_file and not key:
        from pathlib import Path

        for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DMSA_MEMORY_API_KEY":
                key = value.strip().strip('"').strip("'")
    return RemoteMemoryMCPServer(url, key, scope) if url else None
