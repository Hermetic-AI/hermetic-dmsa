"""HTTP 请求日志中间件：给每个请求分配 trace ID 并记录耗时与状态。

日志只写方法、路径、状态码、耗时这类元数据，绝不写请求体、header 值或凭据，
避免把 API key 与用户内容带进日志文件。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from dmsa.observability import get_logger, get_registry, trace_context

_logger = get_logger("dmsa.mcp_server.http.middleware")

# 健康检查每秒都在打，降到 DEBUG，不然 docker logs 全是它。
_QUIET_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/live"})

# 分路径延迟只统计已知路由；任意路径进名字会把指标基数撑爆。
_PATH_METRICS: dict[str, str] = {"/add": "add", "/search": "search"}

# 复用上/下游已有的请求 ID header，方便和调用方日志对齐
_REQUEST_ID_HEADERS: tuple[str, ...] = ("x-request-id", "x-trace-id", "traceparent")


def resolve_trace_id(request: Request) -> str:
    """取上游传入的请求 ID，没有就生成一个短 ID。"""
    for header in _REQUEST_ID_HEADERS:
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip()[:64]
    return uuid.uuid4().hex[:12]


def level_for_status(status_code: int) -> int:
    """状态码 -> 日志级别：5xx 报错、4xx 警告、其余信息级。"""
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


def record_http_metrics(path: str, status_code: int, duration_ms: float) -> None:
    """把一次请求写进 metrics：整体延迟、状态码族计数与分路径延迟。

    健康检查只计数不进延迟直方图，否则探针流量会淹没真实请求的延迟分布。
    """
    registry = get_registry()
    if path in _QUIET_PATHS:
        registry.counter("dmsa.http.health_checks").inc()
        return
    seconds = duration_ms / 1000
    registry.histogram("dmsa.http.latency").observe(seconds)
    registry.counter(f"dmsa.http.requests.{status_code // 100}xx").inc()
    route = _PATH_METRICS.get(path)
    if route is not None:
        registry.histogram(f"dmsa.http.{route}.latency").observe(seconds)


def install_request_logging(app: FastAPI, *, include_started: bool = False) -> None:
    """给 app 装上请求日志中间件。

    Args:
        app: 目标 FastAPI 实例。
        include_started: 是否额外记录 "request started"（默认只在结束时打一条，
            含方法与耗时；打开后能看到卡住的请求）。
    """

    @app.middleware("http")
    async def _log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """记录每个请求的 trace ID、状态与耗时，并回写 X-Request-ID。"""
        trace_id = resolve_trace_id(request)
        method = request.method
        path = request.url.path
        client = _client_ip(request)
        quiet = path in _QUIET_PATHS
        started = time.perf_counter()
        if include_started and not quiet:
            _logger.debug("request started", method=method, path=path, client=client)
        with trace_context(trace_id):
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                record_http_metrics(path, 500, duration_ms)
                _logger.exception(
                    "request failed",
                    method=method,
                    path=path,
                    client=client,
                    duration_ms=duration_ms,
                )
                raise
        status = int(response.status_code)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        record_http_metrics(path, status, duration_ms)
        # 成功的访问行由 uvicorn.access 输出（自带 client:port），这里降到 DEBUG 避免
        # 同一请求打两遍；4xx/5xx 带 trace 与耗时，无论级别配置都要留下。
        level = logging.DEBUG if status < 400 else level_for_status(status)
        _logger.log(
            level,
            "request completed",
            method=method,
            path=path,
            client=client,
            status=status,
            duration_ms=duration_ms,
        )
        response.headers.setdefault("X-Request-ID", trace_id)
        return response


def _client_ip(request: Request) -> str:
    """取客户端 IP，仅用于日志，取不到返回 unknown。"""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host) if host else "unknown"


__all__ = [
    "install_request_logging",
    "level_for_status",
    "record_http_metrics",
    "resolve_trace_id",
]
