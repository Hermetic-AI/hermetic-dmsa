"""Coding benchmark 命令行入口."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmarks.coding.adapters import run_command_agent, run_openai_compatible_agent
from benchmarks.coding.dataset import load_manifest
from benchmarks.coding.evaluator import ARMS, evaluate_fixture
from dmsa.memory.coding.vector_index import VectorIndex


def _agent_factory(args: argparse.Namespace) -> Any:
    """按显式配置返回 agent callable; 不隐式启动付费模型."""
    if args.agent_argv:
        argv = json.loads(args.agent_argv)
        if not isinstance(argv, list) or any(not isinstance(part, str) for part in argv):
            raise ValueError("--agent-argv must be a JSON array of strings")
        return lambda payload: run_command_agent(argv, payload, args.timeout)
    if args.model:
        if not args.api_key_env:
            raise ValueError("--api-key-env is required with --model")
        return lambda payload: run_openai_compatible_agent(
            payload,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
        )
    return None


def _markdown(report: dict[str, Any]) -> str:
    """生成简短、可审计的结果摘要."""
    metrics = report["metrics"]
    lines = [
        "# Coding Memory synthetic proxy evaluation",
        "",
        "> This is not an official competition score. Synthetic retrieval metrics only.",
        "",
        f"Manifest SHA-256: `{report['run_metadata']['manifest_sha256']}`",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {key} | {value if value is not None else 'not measured'} |"
        for key, value in metrics.items()
    )
    lines.extend(["", "## By noise level", ""])
    for level, rows in report["noise_levels"].items():
        recall = sum(row["evidence_recall"] for row in rows) / len(rows) if rows else 0.0
        misleading = sum(row["misleading_memory"] for row in rows) / len(rows) if rows else 0.0
        lines.append(
            f"- `{level}`: evidence recall={recall:.3f}, misleading-memory={misleading:.3f}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    """解析参数, 保存机器可读 JSON 与 Markdown."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="benchmarks/coding/fixtures/mini.json")
    parser.add_argument("--output", default="benchmarks/results/coding-memory-mini.json")
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=["no_memory", "sparse"])
    parser.add_argument("--seed", type=int, default=0)
    agent_group = parser.add_mutually_exclusive_group()
    agent_group.add_argument(
        "--agent-argv", help="JSON argv list; agent reads one JSON object on stdin"
    )
    agent_group.add_argument(
        "--model", help="explicit OpenAI-compatible model name; incurs provider cost"
    )
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--api-key-env", help="name of credential environment variable")
    parser.add_argument("--embedding-model", choices=["text-embedding-v4"])
    parser.add_argument(
        "--embedding-base-url",
        default=os.environ.get("DMSA_BENCH_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument("--embedding-api-key-env", default="DMSA_BENCH_API_KEY")
    parser.add_argument("--embedding-dimension", type=int, default=1536)
    parser.add_argument("--vector-url", help="optional Qdrant URL; no port is exposed by default")
    parser.add_argument("--max-agent-tokens", type=int, default=20_000)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--resume", action="store_true", help="resume from output.json.checkpoint")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.max_agent_tokens <= 0 or (args.max_cost_usd is not None and args.max_cost_usd < 0):
        parser.error("token and cost budgets must be positive (cost may be zero)")
    if args.embedding_dimension <= 0:
        parser.error("--embedding-dimension must be positive")
    if any(arm in {"hybrid", "full"} for arm in args.arms) and not args.embedding_model:
        parser.error("--embedding-model text-embedding-v4 is required for hybrid/full arms")
    manifest = load_manifest(args.manifest)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = output.with_suffix(output.suffix + ".checkpoint") if args.resume else None
    agent_name = args.model or ("command-wrapper" if args.agent_argv else None)
    agent_settings = {
        "model": args.model,
        "base_url_sha256": hashlib.sha256(args.base_url.encode()).hexdigest()
        if args.model
        else None,
        "argv_sha256": hashlib.sha256(args.agent_argv.encode()).hexdigest()
        if args.agent_argv
        else None,
    }
    agent_config_sha256 = (
        hashlib.sha256(json.dumps(agent_settings, sort_keys=True).encode()).hexdigest()
        if agent_name
        else None
    )
    embedder = None
    vector_factory: Callable[[], VectorIndex] | None = None
    embedding_client: Any | None = None
    if args.embedding_model:
        embedding_key = os.environ.get(args.embedding_api_key_env)
        if not embedding_key:
            parser.error(
                f"missing embedding credential environment variable {args.embedding_api_key_env}"
            )
        try:
            import openai
        except ImportError as exc:
            parser.error("install hermetic-dmsa[http] to use a remote embedding model")
            raise AssertionError from exc
        from dmsa.mcp_server.http.embedding import TextEmbeddingV4

        embedding_client = openai.OpenAI(api_key=embedding_key, base_url=args.embedding_base_url)
        embedder = TextEmbeddingV4(embedding_client, args.embedding_dimension)
    embedding_config_sha256 = None
    if args.embedding_model:
        embedding_config_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "model": args.embedding_model,
                    "dimension": args.embedding_dimension,
                    "base_url_sha256": hashlib.sha256(args.embedding_base_url.encode()).hexdigest(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    if args.vector_url:
        if embedder is None:
            parser.error("--vector-url requires --embedding-model")
        from dmsa.memory.coding.vector_index import QdrantVectorIndex

        vector_key = os.environ.get("DMSA_QDRANT_API_KEY") or None

        def vector_factory() -> VectorIndex:
            return QdrantVectorIndex(args.vector_url, api_key=vector_key)

    try:
        report = evaluate_fixture(
            manifest,
            arms=args.arms,
            seed=args.seed,
            agent=_agent_factory(args),
            agent_name=agent_name,
            agent_config_sha256=agent_config_sha256,
            embedder=embedder,
            embedding_config_sha256=embedding_config_sha256,
            vector_index_factory=vector_factory,
            vector_backend="qdrant" if vector_factory is not None else "sqlite",
            vector_config_sha256=hashlib.sha256(args.vector_url.encode()).hexdigest()
            if args.vector_url
            else None,
            max_agent_tokens=args.max_agent_tokens,
            max_cost_usd=args.max_cost_usd,
            checkpoint_path=checkpoint,
        )
    finally:
        if embedding_client is not None:
            embedding_client.close()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
