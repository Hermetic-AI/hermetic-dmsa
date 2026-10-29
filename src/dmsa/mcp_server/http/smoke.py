"""仅用标准库执行健康、写入及检索烟雾检查。."""

from __future__ import annotations

import argparse
import json
import os
from http.client import HTTPException
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dmsa.errors import DMSAError, TransportError
from dmsa.mcp_server.http.runtime import read_secret
from dmsa.observability import add_logging_args, configure_root, get_logger

_CONTENT = "DMSA deployment persistence smoke marker"


def _request(base_url: str, path: str, api_key: str, body: dict[str, Any] | None) -> Any:
    """发出受限时长请求并隐藏上游响应正文。."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = None if body is None else json.dumps(body).encode("utf-8")
    try:
        request = Request(base_url.rstrip("/") + path, data=payload, headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except (HTTPException, URLError, OSError, ValueError) as exc:
        raise TransportError("smoke request failed") from exc


def run_smoke(
    base_url: str,
    api_key: str,
    *,
    request_id: str = "dmsa-deployment-smoke-v1",
    search_only: bool = False,
    health_only: bool = False,
) -> None:
    """验证真实接口契约；search_only 在重启后禁止补写证据。."""
    try:
        parsed = urlsplit(base_url)
    except ValueError as exc:
        raise TransportError("smoke requires a valid HTTP base URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise TransportError("smoke requires an HTTP base URL without credentials")
    if _request(base_url, "/health", "", None) != {"ok": True}:
        raise TransportError("health contract failed")
    if health_only:
        return
    user_id = f"dmsa-deployment-smoke:{request_id}"
    if not search_only:
        response = _request(
            base_url,
            "/add",
            api_key,
            {
                "request_id": request_id,
                "user_id": user_id,
                "session_id": request_id,
                "messages": [{"role": "user", "content": _CONTENT}],
            },
        )
        # 只校验必需字段: 响应可以增量携带 repo 等新键, 不允许缺失或改写既有契约
        required = {
            "success": True,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": request_id,
        }
        if not isinstance(response, dict) or any(
            response.get(key) != value for key, value in required.items()
        ):
            raise TransportError("add contract failed")
    response = _request(
        base_url,
        "/search",
        api_key,
        {
            "query": "deployment persistence smoke marker",
            "user_id": user_id,
            "top_k": 5,
            "options": [],
        },
    )
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise TransportError("search contract failed")
    hits = response["data"]
    if not any(
        isinstance(hit, dict)
        and isinstance(hit.get("content"), str)
        and _CONTENT in hit["content"]
        and f"[request={request_id} " in hit["content"]
        and isinstance(hit.get("id"), str)
        and isinstance(hit.get("score"), (int, float))
        for hit in hits
    ):
        raise TransportError("persisted smoke evidence not found")


def main(argv: list[str] | None = None) -> int:
    """运行探针并仅记录固定成功或失败消息。."""
    parser = argparse.ArgumentParser(prog="dmsa-memory-smoke")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--request-id", default="dmsa-deployment-smoke-v1")
    parser.add_argument("--search-only", action="store_true")
    parser.add_argument("--health-only", action="store_true")
    add_logging_args(parser)
    args = parser.parse_args(argv)
    # 探针的 stderr 是机器消费的固定 JSON 行：CLI > 环境变量 > json 默认
    configure_root(
        level=args.log_level,
        fmt=args.log_format or os.environ.get("DMSA_LOG_FORMAT") or "json",
        log_file=args.log_file,
    )
    try:
        api_key = "" if args.health_only else read_secret("DMSA_MEMORY_API_KEY")
        run_smoke(
            args.url,
            api_key,
            request_id=args.request_id,
            search_only=args.search_only,
            health_only=args.health_only,
        )
    except DMSAError:
        get_logger().error("deployment smoke failed")
        return 1
    get_logger().info("deployment smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
