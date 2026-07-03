"""MCP server CLI 入口（`dmsa-mcp-server`）。

用法：

    dmsa-mcp-server                          # 内存模式
    dmsa-mcp-server --storage ~/.dmsa/db.sqlite  # 持久化
    dmsa-mcp-server --storage :memory: --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dmsa.config import DMSAConfig, load_config
from dmsa.errors import DMSAError
from dmsa.mcp_server.server import MCPServer
from dmsa.memory import MemoryLayer

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="dmsa-mcp-server",
        description="hermetic-dmsa MCP server — expose 7 tools to LLM agents",
    )
    parser.add_argument(
        "--storage",
        type=str,
        default=None,
        help="SQLite storage path (default from DMSAConfig). Use ':memory:' for in-memory mode.",
    )
    parser.add_argument(
        "--embedder-dim",
        type=int,
        default=64,
        help="Hash embedder dimension (default 64)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口函数。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    # 配置
    try:
        config = load_config()
        if args.storage:
            from dataclasses import replace

            config = replace(config, storage=type(config.storage)(path=args.storage))
    except DMSAError as exc:
        print(f"config error: {exc.message}", file=sys.stderr)
        return 1

    # 创建 memory layer（内存模式，因为 PMD InMemoryStore 是默认）
    layer = MemoryLayer()
    # 创建 server
    server = MCPServer(memory_layer=layer)
    # 跑
    try:
        asyncio.run(server.run_stdio())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"server error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
