# ruff: noqa: RUF002, D415
"""Coding Memory HTTP CLI。"""

from __future__ import annotations

import argparse
import os

from dmsa.errors import ConfigError
from dmsa.mcp_server.http.app import create_app
from dmsa.mcp_server.http.runtime import load_settings
from dmsa.memory import CodingMemory
from dmsa.memory.coding.vector_index import QdrantVectorIndex


def build_parser() -> argparse.ArgumentParser:
    """建立 CLI 参数。"""
    parser = argparse.ArgumentParser(prog="dmsa-memory-http")
    parser.add_argument("--profile", choices=("sparse-dev", "competition"), default="sparse-dev")
    parser.add_argument("--storage", default="./dmsa-coding.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key-env", default="DMSA_MEMORY_API_KEY")
    parser.add_argument("--endpoint-env", default="DMSA_EMBEDDING_BASE_URL")
    parser.add_argument("--dimension", type=int, default=1536)
    return parser


def main(argv: list[str] | None = None) -> int:
    """校验 profile 后启动 uvicorn，且关闭自有 memory。"""
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.profile, args.api_key_env, args.endpoint_env, args.dimension)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    client = None
    if args.profile == "competition":
        try:
            import openai
        except ImportError as exc:
            raise SystemExit("install hermetic-dmsa[http] to use competition profile") from exc
        from dmsa.mcp_server.http.embedding import TextEmbeddingV4

        client = openai.OpenAI(
            api_key=settings.embedding_key,
            base_url=settings.endpoint,
            timeout=60,
            max_retries=2,
        )
        embedder = TextEmbeddingV4(client, settings.dimension)
    else:
        embedder = None
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("install hermetic-dmsa[http] to run HTTP server") from exc
    memory: CodingMemory | None = None
    vector_index = None
    try:
        vector_url = os.environ.get("DMSA_VECTOR_URL", "").strip()
        if vector_url:
            if args.profile != "competition":
                raise SystemExit("DMSA_VECTOR_URL is only supported with competition profile")
            try:
                vector_index = QdrantVectorIndex(
                    vector_url,
                    api_key=os.environ.get("DMSA_QDRANT_API_KEY") or None,
                )
            except Exception as exc:
                raise SystemExit("cannot initialize configured Qdrant vector index") from exc
        memory = CodingMemory(args.storage, embedder=embedder, vector_index=vector_index)
        uvicorn.run(
            create_app(memory, settings.api_key),
            host=args.host,
            port=args.port,
            log_config=None,
            access_log=False,
        )
    finally:
        if memory is not None:
            memory.close()
        if client is not None:
            client.close()
    return 0
