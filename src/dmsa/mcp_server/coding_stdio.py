"""CodingMemory 的 stdio MCP 进程装配与收发循环.

进程装配与 stdio 收发从 coding_memory 拆出, 让服务实现文件保持在项目规范的
长度内. 控制台入口仍由 coding_memory.main 暴露, 脚本地址不变.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import TextIO

from dmsa.gradient.integrated import IntegratedRSI
from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import (
    JsonRpcErrorCode,
    make_error_response,
    parse_request,
)
from dmsa.memory import CodingMemory
from dmsa.observability import get_logger, setup_logging

_logger = get_logger("dmsa.mcp_server.coding_stdio")


async def serve(server: CodingMemoryMCPServer, stdin: TextIO, stdout: TextIO) -> None:
    """跨 Windows 与 POSIX 读取行式 stdio JSON-RPC."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        try:
            for line in stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True, name="dmsa-coding-mcp-stdin").start()
    while (line := await queue.get()) is not None:
        try:
            response = await server.handle(parse_request(line))
        except Exception as exc:
            _logger.warning(
                "stdio request rejected",
                error_type=type(exc).__name__,
                reason=str(exc)[:200],
            )
            response = make_error_response(JsonRpcErrorCode.INVALID_REQUEST.value, str(exc))
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def run_server() -> int:
    """以 MCP stdio 模式运行项目级 CodingMemory."""
    # stdout 是 JSON-RPC 通道，日志只能走 stderr（configure_root 的默认目标）。
    level = setup_logging()
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw_path = os.environ.get("DMSA_CODING_MEMORY_PATH", "~/.dmsa/coding-memory.sqlite")
    path = Path(raw_path).expanduser()
    scope = os.environ.get("DMSA_CODING_MEMORY_SCOPE", Path.cwd().name)
    from dmsa.mcp_server.remote_memory import configured_remote

    remote = configured_remote(scope)
    if remote is not None:
        _logger.info(
            "coding memory stdio started",
            mode="remote",
            scope=scope,
            log_level=logging.getLevelName(level),
        )
        try:
            asyncio.run(remote.run_stdio(sys.stdin, sys.stdout))
        except Exception:
            _logger.exception("remote coding memory stdio crashed")
            raise
        finally:
            remote.close()
            _logger.info("coding memory stdio stopped", mode="remote", scope=scope)
        return 0
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    memory = CodingMemory(path)
    rsi = None
    if str(path) != ":memory:":
        try:
            rsi = IntegratedRSI(memory)
        except Exception as exc:
            _logger.warning(
                "rsi disabled",
                error_type=type(exc).__name__,
                reason=str(exc)[:200],
            )
            rsi = None
    server = CodingMemoryMCPServer(memory, scope, path, rsi)
    _logger.info(
        "coding memory stdio started",
        mode="local",
        scope=scope,
        storage=str(path),
        rsi=rsi is not None,
        log_level=logging.getLevelName(level),
    )
    try:
        asyncio.run(serve(server, sys.stdin, sys.stdout))
    except KeyboardInterrupt:
        return 0
    except Exception:
        _logger.exception("coding memory stdio crashed")
        raise
    finally:
        memory.close()
        _logger.info("coding memory stdio stopped", mode="local", scope=scope)
    return 0
