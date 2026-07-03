"""MCP 协议层：JSON-RPC 2.0 消息结构 + 校验。

MCP 规范：[modelcontextprotocol.io](https://modelcontextprotocol.io)
本实现聚焦最小子集：``initialize`` / ``tools/list`` / ``tools/call`` 三个方法。

依赖规则：只 import stdlib + dmsa.errors。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dmsa.errors import InvalidRequestError

__all__ = [
    "JsonRpcVersion",
    "JsonRpcErrorCode",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcError",
    "parse_request",
    "make_response",
    "make_error_response",
]


class JsonRpcVersion(str, Enum):
    """JSON-RPC 2.0 版本。"""

    V2_0 = "2.0"


class JsonRpcErrorCode(int, Enum):
    """JSON-RPC 2.0 标准错误码 + MCP 扩展。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # MCP 扩展
    TOOL_NOT_FOUND = -32000
    TOOL_EXECUTION_ERROR = -32001
    TRANSPORT_ERROR = -32002


@dataclass(frozen=True, slots=True)
class JsonRpcRequest:
    """JSON-RPC 2.0 请求。"""

    jsonrpc: str
    method: str
    params: dict[str, Any]
    id: str | int | None


@dataclass(frozen=True, slots=True)
class JsonRpcResponse:
    """JSON-RPC 2.0 成功响应。"""

    jsonrpc: str
    result: Any
    id: str | int | None


@dataclass(frozen=True, slots=True)
class JsonRpcError:
    """JSON-RPC 2.0 错误响应。"""

    jsonrpc: str
    error_code: int
    error_message: str
    error_data: Any = None
    id: str | int | None = None


def parse_request(payload: str | bytes | dict[str, Any]) -> JsonRpcRequest:
    """解析 JSON-RPC 2.0 请求。"""
    if isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise InvalidRequestError(
                f"invalid JSON: {exc}",
                details={"line": exc.lineno, "col": exc.colno},
            ) from exc
    else:
        data = payload
    if not isinstance(data, dict):
        raise InvalidRequestError(
            "request must be a JSON object",
            details={"type": type(data).__name__},
        )
    if data.get("jsonrpc") != JsonRpcVersion.V2_0.value:
        raise InvalidRequestError(
            f"jsonrpc must be {JsonRpcVersion.V2_0.value!r}",
            details={"jsonrpc": data.get("jsonrpc")},
        )
    method = data.get("method")
    if not isinstance(method, str):
        raise InvalidRequestError(
            "method must be a string",
            details={"method_type": type(method).__name__ if method is not None else "null"},
        )
    params = data.get("params", {})
    if not isinstance(params, dict):
        raise InvalidRequestError(
            "params must be an object",
            details={"params_type": type(params).__name__},
        )
    return JsonRpcRequest(
        jsonrpc=JsonRpcVersion.V2_0.value,
        method=method,
        params=params,
        id=data.get("id"),
    )


def make_response(result: Any, request_id: str | int | None) -> dict[str, Any]:
    """构造 JSON-RPC 2.0 成功响应 dict。"""
    return {
        "jsonrpc": JsonRpcVersion.V2_0.value,
        "result": result,
        "id": request_id,
    }


def make_error_response(
    error_code: int,
    error_message: str,
    *,
    request_id: str | int | None = None,
    error_data: Any = None,
) -> dict[str, Any]:
    """构造 JSON-RPC 2.0 错误响应 dict。"""
    error_obj: dict[str, Any] = {"code": error_code, "message": error_message}
    if error_data is not None:
        error_obj["data"] = error_data
    return {
        "jsonrpc": JsonRpcVersion.V2_0.value,
        "error": error_obj,
        "id": request_id,
    }
