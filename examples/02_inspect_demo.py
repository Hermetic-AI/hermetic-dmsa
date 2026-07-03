"""Demo: 完整真实场景演练 + dmsa-inspect 验证。

模拟一个真实使用场景：
1. 创建一个有持久化存储的 MemoryLayer（用 tempfile）
2. 模拟 Agent 跑几次 dmsa_add_memory + dmsa_feedback
3. 导出为 SQLite 持久化
4. 用 dmsa-inspect CLI 验证数据真的写入 + 反向传播生效

运行：
    cd hermetic-dmsa
    PYTHONPATH=src python examples/02_inspect_demo.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

# Windows 终端编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dmsa.gradient import HashEmbedder  # noqa: E402
from dmsa.mcp_server import MCPServer  # noqa: E402
from dmsa.memory import MemoryLayer  # noqa: E402

OK = "[OK]"
SEP = "=" * 60


def banner(text: str) -> None:
    print(f"\n{SEP}\n  {text}\n{SEP}")


def call(server: MCPServer, method: str, params: dict, request_id: int) -> dict:
    return server.process_request_sync(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    )


def main() -> None:
    banner("Demo 02: 真实场景 + dmsa-inspect 验证")

    # 1. 创建临时 SQLite 数据库 + MemoryLayer
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    db_path = tmp.name
    print(f"  {OK} 临时数据库: {db_path}")

    # PMD 用 SQLiteStore
    from pmd.store.sqlite_store import SQLiteStore
    from pmd.store.schema import apply_schema

    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    conn.close()

    # 用 SQLiteStore 的 MemoryLayer
    store = SQLiteStore(path=db_path)
    layer = MemoryLayer(store=store, embedder=HashEmbedder(dim=64))
    server = MCPServer(memory_layer=layer)
    print(f"  {OK} MemoryLayer 创建完成（持久化到 SQLite）")

    # 2. 模拟 Agent 工作
    banner("Step 1: Agent 写入 5 条记忆")
    nodes = []
    for content, tags, importance in [
        ("user prefers dark mode", ["user_pref"], 0.9),
        ("user is a Python developer", ["user_skill"], 0.7),
        ("project uses React + FastAPI", ["project"], 0.6),
        ("API requires YYYY-MM-DD date format", ["schema_hint"], 0.8),
        ("user dislikes markdown headers", ["user_pref"], 0.5),
    ]:
        r = call(
            server,
            "tools/call",
            {
                "name": "dmsa_add_memory",
                "arguments": {
                    "content": content,
                    "system_prompt": "you are a coding assistant",
                    "tags": tags,
                    "importance": importance,
                    "requires_grad": True,
                },
            },
            len(nodes) + 1,
        )
        nodes.append(r["result"]["node"])
        print(f"  {OK} [{nodes[-1]['id'][:12]}...] {content[:50]}")

    # 3. 模拟 tool call 失败
    banner("Step 2: 模拟 tool call 失败 → 自动 critique")
    for _ in range(3):
        call(
            server,
            "tools/call",
            {
                "name": "dmsa_tool_call",
                "arguments": {
                    "tool_name": "get_date",
                    "args": {"format": "wrong"},
                    "error": "format must be YYYY-MM-DD, got 'wrong'",
                },
            },
            100,
        )
    print(f"  {OK} 3 次失败 tool_call 记录完成")

    # 4. 用户反馈 + SGA 聚合
    banner("Step 3: 用户反馈 → 触发 SGA")
    r = call(
        server,
        "tools/call",
        {
            "name": "dmsa_feedback",
            "arguments": {
                "target_node_id": nodes[3]["id"],  # API requires YYYY-MM-DD
                "feedback_text": "tool description should explicitly mention YYYY-MM-DD format",
                "source": "USER",
                "severity": 0.9,
            },
        },
        200,
    )
    print(f"  {OK} SGA 聚合: {r['result']['cluster_count']} 簇")

    # 5. 打点版本
    banner("Step 4: 打点版本")
    call(
        server,
        "tools/call",
        {
            "name": "dmsa_snapshot",
            "arguments": {"description": "after 5 memories + 3 failed calls", "tag": "demo-v0.1"},
        },
        300,
    )
    print(f"  {OK} snapshot 完成")

    # 6. 关闭 server，触发持久化
    del server
    del layer
    print(f"\n  {OK} server 已关闭，SQLite 数据落盘")

    # 7. 现在用 dmsa-inspect 验证
    banner("Step 5: 用 dmsa-inspect 验证使用生效")

    # 调用 dmsa-inspect CLI（在同一 Python 里调 main）
    from dmsa.inspect.main import main as inspect_main

    print("\n>>> dmsa-inspect dag\n")
    inspect_main(["--storage", db_path, "dag"])

    print("\n>>> dmsa-inspect metrics\n")
    inspect_main(["--storage", db_path, "metrics"])

    print("\n>>> dmsa-inspect versions\n")
    inspect_main(["--storage", db_path, "versions"])

    # 节点详情
    print(f"\n>>> dmsa-inspect node --node-id {nodes[3]['id'][:24]}...\n")
    inspect_main(["--storage", db_path, "node", "--node-id", nodes[3]["id"]])

    # Lineage
    print(f"\n>>> dmsa-inspect lineage --node-id {nodes[3]['id'][:24]}...\n")
    inspect_main(
        ["--storage", db_path, "lineage", "--node-id", nodes[3]["id"], "--depth", "2"]
    )

    # 8. 清理（先关 store 再 unlink；Windows 文件被占用会失败）
    try:
        store.close()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        Path(db_path).unlink(missing_ok=True)
        print(f"\n  {OK} 临时文件已清理")
    except OSError:
        print(f"\n  (Note: 临时文件 {db_path} 被占用，下次重启清理)")
    print(f"\n{SEP}\n  ✓ Demo 完成 — 使用过程检测已生效\n{SEP}")


if __name__ == "__main__":
    main()