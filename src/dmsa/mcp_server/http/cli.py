"""Coding Memory HTTP CLI。"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, NoReturn

from dmsa.errors import ConfigError
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.mcp_server.http.app import create_app
from dmsa.mcp_server.http.runtime import load_settings
from dmsa.memory import CodingMemory
from dmsa.memory.coding.vector_index import QdrantVectorIndex
from dmsa.observability import add_logging_args, apply_cli_logging, get_logger

_logger = get_logger("dmsa.mcp_server.http.cli")


def build_parser() -> argparse.ArgumentParser:
    """建立 CLI 参数。"""
    parser = argparse.ArgumentParser(prog="dmsa-memory-http")
    parser.add_argument("--profile", choices=("sparse-dev", "competition"), default="sparse-dev")
    parser.add_argument("--storage", default="./dmsa-coding.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key-env", default="DMSA_MEMORY_API_KEY")
    parser.add_argument("--endpoint-env", default="DMSA_EMBEDDING_BASE_URL")
    parser.add_argument(
        "--dimension", type=int, default=os.environ.get("DMSA_EMBEDDING_DIMENSION", "1024")
    )
    add_logging_args(parser)
    return parser


def _fatal(message: str) -> NoReturn:
    """先写日志再退出，保证 docker logs 里能看到失败原因。"""
    _logger.error("startup failed", reason=message)
    raise SystemExit(message)


def main(argv: list[str] | None = None) -> int:
    """校验 profile 后启动 uvicorn，且关闭自有 memory。"""
    args = build_parser().parse_args(argv)
    level = apply_cli_logging(args)
    try:
        settings = load_settings(args.profile, args.api_key_env, args.endpoint_env, args.dimension)
    except ConfigError as exc:
        _fatal(str(exc))
    client = None
    embedder: Any | None = None
    if args.profile == "competition":
        if settings.embedding_model == "qwen3.7-text-embedding-flash":
            from dmsa.mcp_server.http.embedding import QwenTextEmbedding

            embedder = QwenTextEmbedding(
                settings.endpoint or "",
                settings.embedding_key or "",
                settings.dimension,
                model=settings.embedding_model,
            )
        else:
            try:
                import openai
            except ImportError:
                _fatal("install hermetic-dmsa[http] to use competition profile")
            from dmsa.mcp_server.http.embedding import TextEmbeddingV4

            client = openai.OpenAI(
                api_key=settings.embedding_key,
                base_url=settings.endpoint,
                timeout=60,
                max_retries=2,
            )
            embedder = TextEmbeddingV4(client, settings.dimension)
    try:
        try:
            import uvicorn
        except ImportError:
            _fatal("install hermetic-dmsa[http] to run HTTP server")
        memory: CodingMemory | None = None
        vector_index = None
        vector_url = os.environ.get("DMSA_VECTOR_URL", "").strip()
        if vector_url:
            if args.profile != "competition":
                _fatal("DMSA_VECTOR_URL is only supported with competition profile")
            try:
                vector_index = QdrantVectorIndex(
                    vector_url,
                    api_key=os.environ.get("DMSA_QDRANT_API_KEY") or None,
                )
            except Exception:
                _logger.error("vector index init failed")
                _fatal("cannot initialize configured Qdrant vector index")
        memory = CodingMemory(args.storage, embedder=embedder, vector_index=vector_index)
        audit = RetrievalFeedbackStore(args.storage)
        try:
            audit.event(
                "system",
                "runtime_started",
                {
                    "rsi_enabled": os.environ.get("DMSA_RSI_ENABLED", "true"),
                    "rsi_models": os.environ.get("DMSA_RSI_MODELS", "false"),
                    "retrieval_profile": args.profile,
                    "embedding_model": settings.embedding_model if embedder else "disabled",
                },
            )
        finally:
            audit.close()
        _log_startup(args, settings, embedder, vector_index, level)
        uvicorn.run(
            create_app(
                memory,
                settings.api_key,
                IntegratedRSI(
                    memory, models=os.environ.get("DMSA_RSI_MODELS", "false").lower() == "true"
                )
                if os.environ.get("DMSA_RSI_ENABLED", "true").lower() == "true"
                else None,
            ),
            host=args.host,
            port=args.port,
            log_config=None,
            # 保留 uvicorn access log（走我们的 handler 输出）；中间件的请求行在
            # DEBUG 级别去重，INFO 级别仍能看到完整的生命周期事件。
            access_log=True,
        )
        _logger.info("http server stopped", host=args.host, port=args.port)
    finally:
        if memory is not None:
            memory.close()
        if client is not None:
            client.close()
        if embedder is not None and hasattr(embedder, "close"):
            embedder.close()
    return 0


def _log_startup(
    args: argparse.Namespace,
    settings: Any,
    embedder: Any | None,
    vector_index: Any | None,
    level: int,
) -> None:
    """打印一条包含全部关键开关的启动摘要（不含任何密钥）。"""
    _logger.info(
        "coding memory http listening",
        url=f"http://{args.host}:{args.port}",
        profile=args.profile,
        storage=str(args.storage),
        dimension=settings.dimension,
        embedding_model=settings.embedding_model if embedder is not None else "disabled",
        vector_index="qdrant" if vector_index is not None else "sqlite",
        rsi_enabled=os.environ.get("DMSA_RSI_ENABLED", "true"),
        rsi_models=os.environ.get("DMSA_RSI_MODELS", "false"),
        log_level=logging.getLevelName(level),
        log_format=os.environ.get("DMSA_LOG_FORMAT", "text"),
    )
