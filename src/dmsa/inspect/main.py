"""``dmsa-inspect`` CLI 入口。

用法：

    dmsa-inspect dag --storage ~/.dmsa/db.sqlite
    dmsa-inspect node --storage ~/.dmsa/db.sqlite --node-id <ULID>
    dmsa-inspect lineage --storage ~/.dmsa/db.sqlite --node-id <ULID> --depth 3
    dmsa-inspect metrics --storage ~/.dmsa/db.sqlite
    dmsa-inspect versions --storage ~/.dmsa/db.sqlite

所有命令支持 ``--json`` 输出机器可读格式。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from dmsa.errors import DMSAError
from dmsa.inspect.cli import (
    inspect_dag,
    inspect_lineage,
    inspect_metrics,
    inspect_node,
    inspect_versions,
    to_json,
    to_text,
)
from dmsa.observability import add_logging_args, apply_cli_logging


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI parser。"""
    parser = argparse.ArgumentParser(
        prog="dmsa-inspect",
        description="hermetic-dmsa inspect — read-only diagnostics tool",
    )
    parser.add_argument(
        "--storage",
        type=str,
        required=True,
        help="SQLite storage path (e.g. ~/.hermetic-dmsa/db.sqlite)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="output JSON instead of human-readable text",
    )
    add_logging_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dag", help="show DAG overview (nodes / edges / types / tags)")

    p_node = sub.add_parser("node", help="show one node's full details")
    p_node.add_argument("--node-id", type=str, required=True)

    p_lineage = sub.add_parser("lineage", help="show lineage tree from a node")
    p_lineage.add_argument("--node-id", type=str, required=True)
    p_lineage.add_argument("--depth", type=int, default=3)
    p_lineage.add_argument("--direction", type=str, default="BOTH", choices=["UP", "DOWN", "BOTH"])

    p_metrics = sub.add_parser("metrics", help="show backward gradient metrics")
    p_metrics.add_argument(
        "--since",
        type=float,
        default=None,
        help="only count gradients after this Unix timestamp",
    )

    sub.add_parser("versions", help="list version snapshots")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    # stdout 留给诊断结果，日志写 stderr
    apply_cli_logging(args)
    try:
        payload: dict[str, Any]
        if args.command == "dag":
            payload = inspect_dag(args.storage)
        elif args.command == "node":
            payload = inspect_node(args.storage, args.node_id)
        elif args.command == "lineage":
            payload = inspect_lineage(
                args.storage,
                args.node_id,
                depth=args.depth,
                direction=args.direction,
            )
        elif args.command == "metrics":
            payload = inspect_metrics(args.storage, since=args.since)
        elif args.command == "versions":
            payload = inspect_versions(args.storage)
        else:
            parser.print_help()
            return 1
        if args.json:
            print(to_json(payload))
        else:
            print(to_text(payload))
        return 0
    except DMSAError as exc:
        print(f"error [{exc.code.value}]: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"  details: {exc.details}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
