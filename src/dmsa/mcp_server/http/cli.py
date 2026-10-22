# ruff: noqa: RUF002, D415
"""Coding Memory HTTP CLI。"""
from __future__ import annotations

import argparse
import os

from dmsa.mcp_server.http.app import create_app
from dmsa.memory import CodingMemory


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
    key = os.environ.get(args.api_key_env, "")
    if not key.strip():
        raise SystemExit(f"required environment variable is unset: {args.api_key_env}")
    if args.profile == "competition":
        endpoint = os.environ.get(args.endpoint_env, "")
        if not endpoint.strip():
            raise SystemExit(f"required environment variable is unset: {args.endpoint_env}")
        if args.dimension <= 0:
            raise SystemExit("embedding dimension must be positive")
        model = os.environ.get("DMSA_EMBEDDING_MODEL", "text-embedding-v4")
        if model != "text-embedding-v4":
            raise SystemExit("competition profile requires text-embedding-v4")
        try:
            import openai
        except ImportError as exc:
            raise SystemExit("install hermetic-dmsa[http] to use competition profile") from exc
        from dmsa.mcp_server.http.embedding import TextEmbeddingV4
        embedding_key = os.environ.get("DMSA_EMBEDDING_API_KEY", "")
        if not embedding_key.strip():
            raise SystemExit("required environment variable is unset: DMSA_EMBEDDING_API_KEY")
        client = openai.OpenAI(api_key=embedding_key, base_url=endpoint)
        embedder = TextEmbeddingV4(client, args.dimension)
    else:
        embedder = None
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("install hermetic-dmsa[http] to run HTTP server") from exc
    memory: CodingMemory | None = None
    try:
        memory = CodingMemory(args.storage, embedder=embedder)
        uvicorn.run(create_app(memory, key), host=args.host, port=args.port, log_config=None)
    finally:
        if memory is not None:
            memory.close()
        if args.profile == "competition" and "client" in locals():
            client.close()
    return 0




