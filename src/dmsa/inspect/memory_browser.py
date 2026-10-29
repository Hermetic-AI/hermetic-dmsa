"""启动本地只读 CodingMemory 浏览页面。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dmsa.inspect.memory_browser_data import read_metadata, read_records
from dmsa.inspect.rsi_data import read_rsi
from dmsa.observability import add_logging_args, apply_cli_logging, get_logger

_logger = get_logger("dmsa.inspect.memory_browser")


class MemoryBrowserHandler(BaseHTTPRequestHandler):
    """提供只读页面与 JSON 浏览接口。"""

    database: Path
    page: bytes

    def do_GET(self) -> None:
        """响应页面、元数据或筛选后的记忆列表。"""
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/rsi":
                self._json(200, read_rsi(self.database))
            elif parsed.path == "/api/meta":
                self._json(200, read_metadata(self.database))
            elif parsed.path == "/api/records":
                self._json(200, read_records(self.database, parse_qs(parsed.query)))
            elif parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(self.page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(self.page)
            else:
                self._json(404, {"error": "not found"})
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        """发送 UTF-8 JSON 响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """不把查询词或 scope 写入终端日志。"""


def build_parser() -> argparse.ArgumentParser:
    """构造只读浏览器 CLI。"""
    parser = argparse.ArgumentParser(prog="dmsa-memory-browser")
    parser.add_argument("--database", default="~/.dmsa/coding-memory.sqlite")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    add_logging_args(parser)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """验证 SQLite 来源后仅在本机启动只读页面。"""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    apply_cli_logging(args)
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    path = Path(args.database).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"DMSA database not found: {path}")
    read_metadata(path)
    handler = type(
        "BoundMemoryBrowserHandler",
        (MemoryBrowserHandler,),
        {
            "database": path,
            "page": files("dmsa.inspect").joinpath("memory-explorer.html").read_bytes(),
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    try:
        _logger.info("memory explorer listening", url=f"http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
