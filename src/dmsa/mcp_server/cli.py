"""MCP server CLI 入口(`dmsa-mcp-server`).

用法:

    dmsa-mcp-server                          # 内存模式
    dmsa-mcp-server --storage ~/.dmsa/db.sqlite  # 持久化
    dmsa-mcp-server --storage :memory: --verbose
"""

from __future__ import annotations

import argparse
import asyncio

from dmsa.config import load_config
from dmsa.errors import DMSAError
from dmsa.mcp_server.server import MCPServer
from dmsa.memory import MemoryLayer
from dmsa.observability import get_logger
from dmsa.types import MemoryLayerConfig

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="dmsa-mcp-server",
        description="hermetic-dmsa MCP server - expose 7 tools to LLM agents",
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
    """CLI 入口函数."""
    from dataclasses import replace

    parser = build_parser()
    args = parser.parse_args(argv)
    # 配置
    try:
        config = load_config()
        if args.storage:
            config = replace(config, storage=type(config.storage)(path=args.storage))
    except DMSAError as exc:
        get_logger(__name__).error("config error", error=exc.message)
        return 1

    # 创建 memory layer:embedder 由 config.embedder.backend 决定
    # (hash = 零依赖默认;openai = 真实语义,需 pip install hermetic-dmsa[llm]).
    # hash 后端的维度用 --embedder-dim 覆盖.
    from dmsa.gradient import build_embedder

    embedder_cfg = (
        replace(config.embedder, dim=args.embedder_dim)
        if config.embedder.backend == "hash"
        else config.embedder
    )
    try:
        layer = MemoryLayer(
            config=MemoryLayerConfig(storage_path=config.storage.path),
            embedder=build_embedder(embedder_cfg),
        )
    except DMSAError as exc:
        get_logger(__name__).error("memory setup error", error=exc.message)
        return 1

    # fusion 后端:默认 static(零依赖);fusion_backend="llm" 时换 LLMFusion
    if config.gradient.fusion_backend == "llm":
        from dmsa.gradient import LLMFusion, SGAAggregator
        from dmsa.mcp_server.handlers.feedback import set_aggregator

        try:
            set_aggregator(
                SGAAggregator(
                    embedder=layer.embedder,
                    fusion=LLMFusion(
                        model=config.gradient.llm_chat_model,
                        base_url=config.gradient.llm_base_url,
                        api_key_env=config.gradient.llm_api_key_env,
                    ),
                )
            )
        except DMSAError as exc:
            get_logger(__name__).error("llm fusion error", error=exc.message)
            return 1

    # 创建 server
    server = MCPServer(memory_layer=layer)
    # 跑
    try:
        asyncio.run(server.run_stdio())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover
        get_logger(__name__).error("server error", error=str(exc))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
