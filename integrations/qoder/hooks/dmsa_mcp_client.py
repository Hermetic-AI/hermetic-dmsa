# ruff: noqa: RUF002, D415
"""通过 stdio JSON-RPC 调用 dmsa-coding-memory MCP server。

L5 集成层禁止直接 ``import dmsa``（见 AGENTS.md 分层规则），因此这里以子进程
方式启动 ``dmsa.mcp_server.coding_memory`` 并走 MCP 协议写入记忆。
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_MODULE = "dmsa.mcp_server.coding_memory"
PROC_TIMEOUT_SECONDS = 20


class MCPInvokeError(RuntimeError):
    """MCP 调用失败。"""


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _read_line(proc: subprocess.Popen[str]) -> dict[str, Any]:
    line = proc.stdout.readline() if proc.stdout else ""
    if not line:
        raise MCPInvokeError("coding memory MCP closed the stream")
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MCPInvokeError(f"invalid MCP response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MCPInvokeError("MCP response is not an object")
    return parsed


def _server_env(scope: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("DMSA_CODING_MEMORY_PATH", str(Path.home() / ".dmsa" / "coding-memory.sqlite"))
    env["DMSA_CODING_MEMORY_SCOPE"] = scope
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def record(scope: str, request_id: str, session_id: str, content: str) -> dict[str, Any]:
    """写入一条 CodingMemory 记录, 返回 server 侧结果。"""
    python = os.environ.get("DMSA_CODING_MEMORY_PYTHON", sys.executable)
    proc = subprocess.Popen(
        [python, "-m", DEFAULT_MODULE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=_server_env(scope),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        proc.stdin.write(
            _encode(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "clientInfo": {"name": "qoder-hook", "version": "0.1.0"},
                        "capabilities": {},
                    },
                }
            )
        )
        proc.stdin.write(_encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        proc.stdin.write(
            _encode(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "dmsa_coding_memory_record",
                        "arguments": {
                            "request_id": request_id,
                            "session_id": session_id,
                            "content": content,
                        },
                    },
                }
            )
        )
        proc.stdin.flush()
        response: dict[str, Any] = {}
        for _ in range(8):
            response = _read_line(proc)
            if response.get("id") == 2:
                break
    finally:
        with contextlib.suppress(OSError):
            proc.stdin.close()
        try:
            proc.wait(timeout=PROC_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
    if "error" in response:
        raise MCPInvokeError(str(response["error"]))
    result = response.get("result")
    if not isinstance(result, dict):
        raise MCPInvokeError("missing MCP result")
    if result.get("isError"):
        text = _text_of(result)
        raise MCPInvokeError(text or "tool reported error")
    return _json_of(result)


def _text_of(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""


def _json_of(result: dict[str, Any]) -> dict[str, Any]:
    raw = _text_of(result)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}
