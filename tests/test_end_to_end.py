"""端到端集成测试：MCP server + MemoryLayer + SGA + ToolBP 完整循环。"""

from __future__ import annotations

import pytest

from dmsa.gradient import HashEmbedder
from dmsa.mcp_server import MCPServer
from dmsa.memory import MemoryLayer
from dmsa.toolbp import ToolBPEngine, make_schema_node
from dmsa.types import FeedbackSignal


@pytest.fixture
def e2e_server() -> MCPServer:
    """完整 E2E 测试用 server。"""
    embedder = HashEmbedder(dim=32)
    layer = MemoryLayer(embedder=embedder)
    return MCPServer(memory_layer=layer)


def _call(server: MCPServer, method: str, params: dict, request_id: int = 1) -> dict:
    """同步调用。"""
    return server.process_request_sync(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    )


def test_e2e_write_feedback_query(e2e_server: MCPServer) -> None:
    """完整循环：write → feedback → query。"""
    # 1. 写入偏好
    r1 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {
                "content": "user prefers dark mode in all UIs",
                "tags": ["user_pref"],
                "importance": 0.9,
            },
        },
        1,
    )
    assert "error" not in r1
    node_id = r1["result"]["node"]["id"]

    # 2. 写入 derived
    r2 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {
                "content": "use dark theme in generated UIs",
                "parents": [node_id],
                "importance": 0.7,
            },
        },
        2,
    )
    child_id = r2["result"]["node"]["id"]

    # 3. 用户反馈 → SGA
    r3 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_feedback",
            "arguments": {
                "target_node_id": node_id,
                "feedback_text": "should be more verbose in explanations",
                "severity": 0.7,
            },
        },
        3,
    )
    assert "error" not in r3
    assert r3["result"]["cluster_count"] >= 1

    # 4. lineage 查询
    r4 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_query_lineage",
            "arguments": {"node_id": child_id, "depth": 3, "direction": "UP"},
        },
        4,
    )
    ancestor_ids = {n["id"] for n in r4["result"]["nodes"]}
    assert node_id in ancestor_ids


def test_e2e_tool_call_triggers_schema_update(e2e_server: MCPServer) -> None:
    """tool call 失败 → critique → 手动用 ToolBPEngine 更新 schema。"""
    # 1. 记录 tool call 失败
    r1 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_tool_call",
            "arguments": {
                "tool_name": "get_date",
                "args": {"format": "wrong"},
                "error": "format must be YYYY-MM-DD",
            },
        },
        1,
    )
    assert r1["result"]["critique"]["category"] == "schema"

    # 2. 手动用 ToolBPEngine 更新 schema
    engine = ToolBPEngine()
    schema = make_schema_node(
        schema_id="schema-1",
        name="get_date",
        schema_dict={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "a date string"},
            },
        },
    )
    feedback = FeedbackSignal(
        feedback_id="fb-1",
        target_node_id="get_date",
        feedback_text=r1["result"]["critique"]["feedback_text"],
        severity=0.9,
        source="HEURISTIC",
    )
    new_schema = engine.project(schema=schema, feedback=feedback)
    # schema 应当被更新（version+1）
    assert new_schema.version == 2


def test_e2e_snapshot_rollback(e2e_server: MCPServer) -> None:
    """snapshot + rollback 完整流程。"""
    # 1. 写入
    _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {"content": "v1", "system_prompt": "s"},
        },
        1,
    )
    # 2. snapshot
    r_snap = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_snapshot",
            "arguments": {"description": "after v1", "tag": "v1.0"},
        },
        2,
    )
    version_id = r_snap["result"]["version_id"]

    # 3. 再写入
    _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {"content": "v2", "system_prompt": "s"},
        },
        3,
    )

    # 4. rollback (通过 dag property)
    e2e_server._memory.rollback(version_id)
    # 5. 验证只回 v1
    assert len(e2e_server._memory) == 1
    remaining_nodes = list(e2e_server._memory.dag._nodes.values())
    assert any(n.content == "v1" for n in remaining_nodes)
    assert not any(n.content == "v2" for n in remaining_nodes)


def test_e2e_multiple_feedbacks_aggregate(e2e_server: MCPServer) -> None:
    """多次反馈聚合到同一节点。"""
    # 写入
    r = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {"content": "summary", "system_prompt": "s"},
        },
        1,
    )
    node_id = r["result"]["node"]["id"]
    # 多次反馈
    for fb_text in [
        "should be more detailed",
        "should be more verbose",
        "include code examples",
    ]:
        r2 = _call(
            e2e_server,
            "tools/call",
            {
                "name": "dmsa_feedback",
                "arguments": {
                    "target_node_id": node_id,
                    "feedback_text": fb_text,
                    "severity": 0.6,
                },
            },
            2,
        )
        assert "error" not in r2


def test_e2e_feedback_apply_gradient(e2e_server: MCPServer) -> None:
    """完整闭环：write → feedback → apply_gradient → successor 可追溯、可回滚。"""
    # 1. 写入一条可微记忆
    r1 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {
                "content": "answer with conclusion first",
                "requires_grad": True,
                "grad_learning_rate": 0.1,
            },
        },
        1,
    )
    node_id = r1["result"]["node"]["id"]

    # 2. apply 前打点快照
    r_snap = _call(
        e2e_server,
        "tools/call",
        {"name": "dmsa_snapshot", "arguments": {"tag": "before-apply"}},
        2,
    )
    version_id = r_snap["result"]["version_id"]

    # 3. 反馈 → SGA 聚合
    r3 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_feedback",
            "arguments": {
                "target_node_id": node_id,
                "feedback_text": "answers are too long, be more concise",
            },
        },
        3,
    )
    fb = r3["result"]

    # 4. 应用梯度 → 生成 successor
    r4 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_apply_gradient",
            "arguments": {
                "feedback_id": fb["feedback_id"],
                "cluster_id": fb["fused_gradients"][0]["cluster_id"],
                "target_id": node_id,
            },
        },
        4,
    )
    assert "error" not in r4
    assert r4["result"]["applied"] is True
    successor_id = r4["result"]["new_node_id"]

    # 5. successor 融合原内容 + 梯度，lineage 可追溯
    r5 = _call(
        e2e_server,
        "tools/call",
        {
            "name": "dmsa_query_lineage",
            "arguments": {"node_id": successor_id, "direction": "UP"},
        },
        5,
    )
    assert node_id in {n["id"] for n in r5["result"]["nodes"]}

    # 6. 回滚到 apply 前的快照，successor 消失
    e2e_server._memory.rollback(version_id)
    assert successor_id not in e2e_server._memory
    assert node_id in e2e_server._memory
