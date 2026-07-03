"""Example 01: 端到端 MCP 快速启动。

演示 hermetic-dmsa 的完整工作流：
1. 启动 MCP server（内存模式）
2. 模拟 Agent 调用 dmsa_add_memory 写入记忆
3. 模拟 tool call 失败 → dmsa_tool_call 自动生成 critique
4. 用 dmsa_feedback 触发 SGA 聚合
5. 用 dmsa_query_lineage 验证 lineage
6. 用 dmsa_snapshot 打点版本
7. 关掉 server

运行：
    cd hermetic-dmsa
    PYTHONPATH=src python examples/01_quickstart_mcp.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 终端编码修复（避免中文/emoji 报错）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 把 src 加到 PYTHONPATH（不依赖 pip install）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dmsa.gradient import HashEmbedder  # noqa: E402
from dmsa.mcp_server import MCPServer  # noqa: E402
from dmsa.memory import MemoryLayer  # noqa: E402


OK = "[OK]"
SEP = "=" * 60


def banner(text: str) -> None:
    """打印分隔 banner。"""
    print("\n" + SEP)
    print(f"  {text}")
    print(SEP)


def call(server: MCPServer, method: str, params: dict, request_id: int) -> dict:
    """同步调用 server 方法。"""
    response = server.process_request_sync(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    )
    if "error" in response:
        print(f"  [ERROR] {response['error']}")
    return response


def main() -> None:
    """主流程。"""
    banner("hermetic-dmsa 端到端 demo")
    print("  本 demo 演示完整的 'write -> call -> fail -> feedback -> aggregate' 循环。\n")

    # 1. 创建 server（内存模式）
    embedder = HashEmbedder(dim=32)
    layer = MemoryLayer(embedder=embedder)
    server = MCPServer(memory_layer=layer)
    print(f"  {OK} MCP server 创建成功（内存模式）")

    # 2. 写入第一条记忆
    banner("Step 1: 写入 user 偏好")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_add_memory",
            "arguments": {
                "content": "user prefers dark mode",
                "system_prompt": "you are a UI assistant",
                "user_prompt": "remember: I like dark mode",
                "tags": ["user_pref"],
                "importance": 0.9,
            },
        },
        1,
    )
    parent_id = r["result"]["node"]["id"]
    print(f"  {OK} add_memory OK, node_id = {parent_id}")

    # 3. 模拟一次 tool call 失败
    banner("Step 2: 模拟 tool call 失败")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_tool_call",
            "arguments": {
                "tool_name": "get_date",
                "args": {"format": "wrong"},
                "error": "format must be YYYY-MM-DD",
            },
        },
        2,
    )
    print(f"  {OK} tool_call 记录: {r['result']['record']['tool_name']}")
    print(f"  {OK} critique category: {r['result']['critique']['category']}")
    print(f"  {OK} critique: {r['result']['critique']['feedback_text'][:80]}")

    # 4. 触发反馈 + SGA 聚合
    banner("Step 3: 用户反馈 -> SGA 聚合")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_feedback",
            "arguments": {
                "target_node_id": parent_id,
                "feedback_text": "should be more specific about date format YYYY-MM-DD",
                "source": "USER",
                "severity": 0.8,
            },
        },
        3,
    )
    result_data = r["result"]
    print(f"  {OK} feedback 处理完成: {result_data['cluster_count']} 个簇")
    if result_data["fused_gradients"]:
        fused = result_data["fused_gradients"][0]
        print(f"  {OK} fused gradient 置信度: {fused['confidence']:.2f}")
        print(f"  {OK} has_conflict: {fused['has_conflict']}")

    # 5. 查询 lineage
    banner("Step 4: 查询 lineage")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_query_lineage",
            "arguments": {"node_id": parent_id, "depth": 2, "direction": "UP"},
        },
        4,
    )
    print(f"  {OK} 找到 {r['result']['count']} 个祖先节点")

    # 6. 检索
    banner("Step 5: 检索 (lineage-aware search)")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_search",
            "arguments": {"query": "dark mode", "k": 3, "depth": 1},
        },
        5,
    )
    print(f"  {OK} 检索到 {r['result']['count']} 条相关记忆")
    for n in r["result"]["nodes"][:3]:
        print(f"    - {n['content'][:60]} (importance={n['importance']:.2f})")

    # 7. 打点版本
    banner("Step 6: 打点版本")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_snapshot",
            "arguments": {"description": "after first feedback", "tag": "v0.1"},
        },
        6,
    )
    print(f"  {OK} version_id = {r['result']['version_id']}")
    print(f"  {OK} tag = {r['result']['tag']}")

    # 8. 总结
    banner("Demo 完成")
    print("  完整循环走通：写入 -> tool call -> critique -> feedback -> 聚合 -> 检索")
    print("\n  后续可以：")
    print("    1. 在 Claude Code 中配置 .mcp.json 接入（见 integrations/claude-code/）")
    print("    2. 跑 dmsa-mcp-server 子进程，让真实 Agent 通过 stdio JSON-RPC 调用")
    print()


if __name__ == "__main__":
    main()
