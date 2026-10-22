"""stdio 传输层的真实子进程集成测试。

直接拉起 ``python -m dmsa.mcp_server.cli`` 子进程，通过 stdin/stdout
走 newline-delimited JSON-RPC。这个测试的存在意义：MCPServer 的单元测试
都绕开 stdio 直接调 ``handle_request``，只有子进程测试能覆盖
``run_stdio`` 的跨平台行为（Windows 上 ``connect_read_pipe`` 接管 stdin
管道会直接崩溃）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

_TIMEOUT = 30


def _server_env() -> dict[str, str]:
    """子进程环境：继承当前环境，PYTHONPATH 指向 src。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    return env


def _rpc_line(method: str, request_id: int, params: dict | None = None) -> str:
    """构造一行 JSON-RPC 请求。"""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )


@pytest.fixture
def server_proc():
    """拉起 MCP server 子进程（内存模式），测试结束后确保回收。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "dmsa.mcp_server.cli", "--storage", ":memory:"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=_server_env(),
    )
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=_TIMEOUT)


def _send(proc: subprocess.Popen, line: str) -> dict:
    """写一行请求，读一行响应。"""
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    reply = proc.stdout.readline()
    assert reply, "server closed stdout without responding"
    return json.loads(reply)


def test_stdio_initialize_and_tools_list(server_proc: subprocess.Popen) -> None:
    """initialize + tools/list 走真实 stdio 管道。"""
    r = _send(
        server_proc,
        _rpc_line(
            "initialize",
            1,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "0"},
            },
        ),
    )
    assert r["id"] == 1
    assert r["result"]["serverInfo"]["name"] == "hermetic-dmsa"

    r = _send(server_proc, _rpc_line("tools/list", 2))
    tool_names = {t["name"] for t in r["result"]["tools"]}
    assert "dmsa_add_memory" in tool_names
    assert len(tool_names) == 7


def test_stdio_add_memory_and_search(server_proc: subprocess.Popen) -> None:
    """tools/call 全链路：写入记忆 → 检索（server 默认 embedder 生效）。"""
    r = _send(
        server_proc,
        _rpc_line(
            "tools/call",
            1,
            {
                "name": "dmsa_add_memory",
                "arguments": {"content": "user prefers dark mode"},
            },
        ),
    )
    assert "error" not in r, f"got error: {r.get('error')}"
    assert r["result"]["node"]["id"]

    r = _send(
        server_proc,
        _rpc_line(
            "tools/call",
            2,
            {"name": "dmsa_search", "arguments": {"query": "dark mode"}},
        ),
    )
    assert "error" not in r, f"got error: {r.get('error')}"
    assert r["result"]["count"] >= 1


def test_stdio_clean_exit_on_eof(server_proc: subprocess.Popen) -> None:
    """stdin 关闭（EOF）后 server 进程干净退出。"""
    _send(server_proc, _rpc_line("ping", 1))
    assert server_proc.stdin is not None
    server_proc.stdin.close()
    return_code = server_proc.wait(timeout=_TIMEOUT)
    assert return_code == 0
