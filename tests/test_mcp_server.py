"""L4 MCP server 测试。"""

from __future__ import annotations

import json

import pytest

from dmsa.errors import InvalidRequestError
from dmsa.gradient import HashEmbedder
from dmsa.mcp_server import MCPServer
from dmsa.mcp_server.protocol import (
    JsonRpcErrorCode,
    parse_request,
)
from dmsa.mcp_server.tools import (
    DMSA_TOOLS,
    TOOL_ADD_MEMORY,
    TOOL_FEEDBACK,
    TOOL_SNAPSHOT,
)
from dmsa.memory import MemoryLayer


@pytest.fixture
def server() -> MCPServer:
    """测试用 MCPServer（内存模式）。"""
    embedder = HashEmbedder(dim=32)
    layer = MemoryLayer(embedder=embedder)
    return MCPServer(memory_layer=layer)


# ============================================================================
# protocol 层
# ============================================================================


def test_parse_request_valid() -> None:
    """合法 JSON-RPC 请求解析成功。"""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 1,
        }
    )
    req = parse_request(payload)
    assert req.method == "tools/list"
    assert req.id == 1


def test_parse_request_invalid_json_raises() -> None:
    """非法 JSON 抛 :class:`InvalidRequestError`。"""
    with pytest.raises(InvalidRequestError):
        parse_request("not json")


def test_parse_request_wrong_version_raises() -> None:
    """非 2.0 抛错。"""
    with pytest.raises(InvalidRequestError):
        parse_request({"jsonrpc": "1.0", "method": "x", "params": {}, "id": 1})


def test_parse_request_missing_method_raises() -> None:
    """缺 method 抛错。"""
    with pytest.raises(InvalidRequestError):
        parse_request({"jsonrpc": "2.0", "params": {}, "id": 1})


def test_parse_request_params_must_be_object() -> None:
    """params 必须是 dict。"""
    with pytest.raises(InvalidRequestError):
        parse_request({"jsonrpc": "2.0", "method": "x", "params": [1, 2], "id": 1})


# ============================================================================
# server 端 initialize / tools/list
# ============================================================================


def test_initialize_returns_server_info(server: MCPServer) -> None:
    """initialize 返回 server info + capabilities。"""
    request = parse_request(
        {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}
    )
    response = server.process_request_sync(
        {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "hermetic-dmsa"
    assert "tools" in response["result"]["capabilities"]


def test_tools_list_returns_7_tools(server: MCPServer) -> None:
    """tools/list 返回 7 个工具。"""
    response = server.process_request_sync(
        {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}
    )
    assert response["result"]["tools"]
    tool_names = {t["name"] for t in response["result"]["tools"]}
    assert "dmsa_add_memory" in tool_names
    assert "dmsa_query_lineage" in tool_names
    assert "dmsa_feedback" in tool_names
    assert "dmsa_apply_gradient" in tool_names
    assert "dmsa_tool_call" in tool_names
    assert "dmsa_snapshot" in tool_names
    assert "dmsa_search" in tool_names


def test_ping_returns_empty_result(server: MCPServer) -> None:
    """ping 返回空 result。"""
    response = server.process_request_sync(
        {"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 3}
    )
    assert response["result"] == {}


def test_unknown_method_returns_method_not_found(server: MCPServer) -> None:
    """未知 method 返回 -32601。"""
    response = server.process_request_sync(
        {"jsonrpc": "2.0", "method": "unknown_method", "params": {}, "id": 4}
    )
    assert response["error"]["code"] == JsonRpcErrorCode.METHOD_NOT_FOUND.value


# ============================================================================
# tools/call
# ============================================================================


def test_dmsa_add_memory_tool(server: MCPServer) -> None:
    """dmsa_add_memory 工具可调用。"""
    response = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {
                    "content": "hello world",
                    "system_prompt": "you are X",
                    "tags": ["test"],
                    "importance": 0.8,
                },
            },
            "id": 10,
        }
    )
    assert "error" not in response, f"got error: {response.get('error')}"
    node = response["result"]["node"]
    assert node["content"] == "hello world"
    assert "test" in node["tags"]
    assert node["importance"] == 0.8


def test_dmsa_add_memory_empty_content_returns_error(server: MCPServer) -> None:
    """空 content 返回 -32001。"""
    response = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {"content": ""},
            },
            "id": 11,
        }
    )
    assert response["error"]["code"] == JsonRpcErrorCode.TOOL_EXECUTION_ERROR.value


def test_dmsa_query_lineage_tool(server: MCPServer) -> None:
    """dmsa_query_lineage 工具可调用。"""
    # 先 add
    r1 = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {"content": "parent", "system_prompt": "p"},
            },
            "id": 20,
        }
    )
    parent_id = r1["result"]["node"]["id"]
    r2 = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {
                    "content": "child",
                    "system_prompt": "c",
                    "parents": [parent_id],
                },
            },
            "id": 21,
        }
    )
    child_id = r2["result"]["node"]["id"]
    # query
    r3 = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_query_lineage",
                "arguments": {"node_id": child_id, "depth": 2, "direction": "UP"},
            },
            "id": 22,
        }
    )
    assert r3["result"]["count"] >= 1
    assert any(n["id"] == parent_id for n in r3["result"]["nodes"])


def test_dmsa_feedback_tool(server: MCPServer) -> None:
    """dmsa_feedback 触发 SGA 聚合。"""
    r1 = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {"content": "x", "system_prompt": "s"},
            },
            "id": 30,
        }
    )
    node_id = r1["result"]["node"]["id"]
    r2 = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_feedback",
                "arguments": {
                    "target_node_id": node_id,
                    "feedback_text": "this is feedback",
                    "severity": 0.8,
                },
            },
            "id": 31,
        }
    )
    assert r2["result"]["cluster_count"] >= 1


def test_dmsa_tool_call_records_critique(server: MCPServer) -> None:
    """dmsa_tool_call 记录 tool call + 生成 critique。"""
    r = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_tool_call",
                "arguments": {
                    "tool_name": "get_date",
                    "args": {"format": "wrong"},
                    "error": "format must be YYYY-MM-DD",
                },
            },
            "id": 40,
        }
    )
    assert r["result"]["record"]["tool_name"] == "get_date"
    assert r["result"]["record"]["has_error"] is True
    assert r["result"]["critique"]["category"] == "schema"


def test_dmsa_snapshot_tool(server: MCPServer) -> None:
    """dmsa_snapshot 打点版本。"""
    # 先 add 一个 node
    server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_add_memory",
                "arguments": {"content": "x", "system_prompt": "s"},
            },
            "id": 50,
        }
    )
    r = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_snapshot",
                "arguments": {"description": "test", "tag": "v0.1"},
            },
            "id": 51,
        }
    )
    assert r["result"]["version_id"]
    assert r["result"]["tag"] == "v0.1"


def test_dmsa_search_tool(server: MCPServer) -> None:
    """dmsa_search 检索（需要 embedder）。"""
    # add 几个 nodes
    for content in ["apple banana", "quantum physics", "apple fruit"]:
        server.process_request_sync(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "dmsa_add_memory",
                    "arguments": {"content": content, "system_prompt": "s"},
                },
                "id": 60,
            }
        )
    r = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_search",
                "arguments": {"query": "apple", "k": 2},
            },
            "id": 61,
        }
    )
    assert r["result"]["count"] >= 1


def test_dmsa_apply_gradient_tool(server: MCPServer) -> None:
    """dmsa_apply_gradient 简化版返回 applied=True。"""
    r = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "dmsa_apply_gradient",
                "arguments": {
                    "feedback_id": "fb-x",
                    "cluster_id": 0,
                    "target_type": "prompt",
                    "target_id": "some-prompt-id",
                },
            },
            "id": 70,
        }
    )
    assert r["result"]["applied"] is True


def test_unknown_tool_returns_tool_not_found(server: MCPServer) -> None:
    """未知工具返回 -32000。"""
    r = server.process_request_sync(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "unknown_tool", "arguments": {}},
            "id": 80,
        }
    )
    assert r["error"]["code"] == JsonRpcErrorCode.TOOL_NOT_FOUND.value


# ============================================================================
# 全局 DMSA_TOOLS 注册
# ============================================================================


def test_all_7_tools_registered() -> None:
    """7 个工具全部注册到 DMSA_TOOLS。"""
    expected = {
        TOOL_ADD_MEMORY,
        "dmsa_query_lineage",
        TOOL_FEEDBACK,
        "dmsa_apply_gradient",
        "dmsa_tool_call",
        TOOL_SNAPSHOT,
        "dmsa_search",
    }
    actual = set(DMSA_TOOLS.keys())
    assert expected.issubset(actual), f"missing: {expected - actual}"


# 抑制 unused import warning
_ = (TOOL_ADD_MEMORY, TOOL_FEEDBACK, TOOL_SNAPSHOT)
