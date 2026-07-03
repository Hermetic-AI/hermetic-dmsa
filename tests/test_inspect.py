"""dmsa-inspect 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dmsa.errors import LineageNotFoundError, MemoryLayerError
from dmsa.gradient import HashEmbedder
from dmsa.inspect import (
    inspect_dag,
    inspect_lineage,
    inspect_metrics,
    inspect_node,
    inspect_versions,
)
from dmsa.inspect.cli import open_readonly_db, to_json, to_text
from dmsa.inspect.main import main as inspect_main
from dmsa.memory import MemoryLayer, create_prompt_context


@pytest.fixture
def populated_db(tmp_path: Path) -> str:
    """填充一个 SQLite 数据库用于 inspect。"""
    from pmd.store.sqlite_store import SQLiteStore

    db_path = tmp_path / "test_inspect.sqlite"
    # PMD 的 SQLiteStore 默认 schema 用 WAL；inspect 用只读连接
    # 但 populate 时需要可写。用普通连接即可。
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    # 手动建表（避免依赖 PMD store 的 WAL 行为）
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_nodes (
            id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 1,
            content TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'TEXT',
            embedding BLOB,
            embedding_model TEXT,
            structured_payload TEXT,
            creator_prompt_id TEXT NOT NULL,
            creator_agent_id TEXT NOT NULL,
            creation_context TEXT NOT NULL DEFAULT '{}',
            requires_grad INTEGER NOT NULL DEFAULT 0,
            grad_learning_rate REAL NOT NULL DEFAULT 0.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_accessed_at REAL NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            ttl REAL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            checkpoint_id TEXT,
            lineage_depth INTEGER NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 0.5,
            tags TEXT NOT NULL DEFAULT '[]',
            accumulated_feedback TEXT NOT NULL DEFAULT '',
            feedback_count INTEGER NOT NULL DEFAULT 0,
            last_feedback_at REAL
        );
        CREATE TABLE IF NOT EXISTS creator_edges (
            from_node_id TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            confidence REAL NOT NULL DEFAULT 1.0,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            feedback_text TEXT NOT NULL DEFAULT '',
            feedback_token_cost INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (from_node_id, to_node_id, edge_type)
        );
        CREATE TABLE IF NOT EXISTS prompt_contexts (
            prompt_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            parent_prompt_id TEXT,
            system_prompt TEXT NOT NULL DEFAULT '',
            user_prompt TEXT NOT NULL DEFAULT '',
            llm_response TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            temperature REAL NOT NULL DEFAULT 0.0,
            request_timestamp REAL NOT NULL,
            response_timestamp REAL,
            token_usage TEXT NOT NULL DEFAULT '{}',
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            tool_calls TEXT NOT NULL DEFAULT '[]',
            retrieved_memory_ids TEXT NOT NULL DEFAULT '[]',
            few_shot_memory_ids TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS backward_gradients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ancestor_node_id TEXT NOT NULL,
            descendant_node_id TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            gradient_text TEXT NOT NULL,
            llm_used TEXT NOT NULL,
            token_cost INTEGER NOT NULL,
            feedback_source TEXT NOT NULL,
            severity REAL NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS version_snapshots (
            version_id TEXT PRIMARY KEY,
            parent_version_id TEXT,
            created_at REAL NOT NULL,
            dag_state_ref TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            tag TEXT,
            node_count INTEGER NOT NULL DEFAULT 0,
            edge_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL DEFAULT ''
        );
        """
    )
    # 填充节点
    cursor.executemany(
        """
        INSERT INTO memory_nodes (id, version, content, content_type, creator_prompt_id, creator_agent_id, creation_context, requires_grad, grad_learning_rate, created_at, updated_at, last_accessed_at, status, importance, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("node-a", 1, "first memory", "TEXT", "p1", "agent1", "{}", 0, 0.0, 100.0, 100.0, 100.0, "ACTIVE", 0.9, '["user_pref","important"]'),
            ("node-b", 1, "second memory", "TEXT", "p2", "agent1", "{}", 1, 0.1, 110.0, 110.0, 110.0, "ACTIVE", 0.5, '["derived"]'),
            ("node-c", 1, "third memory", "TEXT", "p3", "agent2", "{}", 0, 0.0, 120.0, 120.0, 120.0, "TOMBSTONED", 0.2, "[]"),
        ],
    )
    # 填充边
    cursor.execute(
        "INSERT INTO creator_edges VALUES (?, ?, ?, 1.0, 1.0, '{}', 100.0, '', 0)",
        ("node-a", "node-b", "DERIVED_FROM"),
    )
    # 填充 prompt context
    cursor.execute(
        "INSERT INTO prompt_contexts VALUES (?, ?, NULL, ?, ?, '', 'gpt-4o', 0.7, 100.0, NULL, '{}', 1, NULL, '[]', '[]', '[]')",
        ("p1", "agent1", "you are a helper", "hello"),
    )
    # 填充 gradient
    cursor.execute(
        "INSERT INTO backward_gradients (ancestor_node_id, descendant_node_id, edge_id, gradient_text, llm_used, token_cost, feedback_source, severity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("node-a", "node-b", "(node-a,node-b,DERIVED_FROM)", "be more verbose", "static", 5, "USER", 0.8, 150.0),
    )
    # 填充 version
    cursor.execute(
        "INSERT INTO version_snapshots VALUES (?, NULL, 120.0, 'hash-abc', 'test snapshot', 'v0.1', 3, 1, 'sha256-xyz')",
        ("snap-1",),
    )
    conn.commit()
    conn.close()
    return str(db_path)


# ============================================================================
# open_readonly_db
# ============================================================================


def test_open_readonly_db(populated_db: str) -> None:
    """只读连接成功。"""
    conn = open_readonly_db(populated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM memory_nodes")
    assert cursor.fetchone()[0] == 3
    conn.close()


def test_open_readonly_db_in_memory_rejected() -> None:
    """:memory: 不允许 inspect。"""
    with pytest.raises(MemoryLayerError):
        open_readonly_db(":memory:")


def test_open_readonly_db_nonexistent_raises() -> None:
    """不存在的路径抛错。"""
    with pytest.raises(MemoryLayerError):
        open_readonly_db("/nonexistent/path/to/db.sqlite")


# ============================================================================
# inspect_dag
# ============================================================================


def test_inspect_dag_returns_counts(populated_db: str) -> None:
    """DAG 概况返回正确计数。"""
    d = inspect_dag(populated_db)
    assert d["node_count"] == 3
    assert d["edge_count"] == 1
    assert d["node_types"]["TEXT"] == 3
    assert d["status_counts"]["ACTIVE"] == 2
    assert d["status_counts"]["TOMBSTONED"] == 1


def test_inspect_dag_returns_top_tags(populated_db: str) -> None:
    """top_tags 按计数排序。"""
    d = inspect_dag(populated_db)
    tag_dict = dict(d["top_tags"])
    assert "user_pref" in tag_dict
    assert tag_dict["user_pref"] == 1


def test_inspect_dag_returns_creator_agents(populated_db: str) -> None:
    """creator_agents 列出 top 10。"""
    d = inspect_dag(populated_db)
    agents = dict(d["creator_agents"])
    assert "agent1" in agents
    assert agents["agent1"] == 2


def test_inspect_dag_returns_time_range(populated_db: str) -> None:
    """first_node_at / last_node_at 正确。"""
    d = inspect_dag(populated_db)
    assert d["first_node_at"] == 100.0
    assert d["last_node_at"] == 120.0


# ============================================================================
# inspect_node
# ============================================================================


def test_inspect_node_basic(populated_db: str) -> None:
    """查询单个节点返回完整信息。"""
    n = inspect_node(populated_db, "node-a")
    assert n["id"] == "node-a"
    assert n["content"] == "first memory"
    assert n["importance"] == 0.9
    assert "user_pref" in n["tags"]


def test_inspect_node_includes_creator_prompt(populated_db: str) -> None:
    """creator_prompt 关联数据包含。"""
    n = inspect_node(populated_db, "node-a")
    assert "creator_prompt" in n
    assert n["creator_prompt"]["model_name"] == "gpt-4o"
    assert n["creator_prompt"]["system_prompt"] == "you are a helper"


def test_inspect_node_includes_edges(populated_db: str) -> None:
    """parents / children edges 列出。"""
    n = inspect_node(populated_db, "node-a")
    assert len(n["parents_edges"]) == 0
    assert len(n["children_edges"]) == 1


def test_inspect_node_includes_recent_gradients(populated_db: str) -> None:
    """recent_gradients 列出最近的 backward gradients。"""
    n = inspect_node(populated_db, "node-a")
    assert len(n["recent_gradients"]) >= 1
    assert n["recent_gradients"][0]["feedback_source"] == "USER"


def test_inspect_node_not_found_raises(populated_db: str) -> None:
    """不存在节点抛 :class:`LineageNotFoundError`。"""
    with pytest.raises(LineageNotFoundError):
        inspect_node(populated_db, "nonexistent")


# ============================================================================
# inspect_lineage
# ============================================================================


def test_inspect_lineage_down(populated_db: str) -> None:
    """lineage DOWN 找到子节点。"""
    result = inspect_lineage(populated_db, "node-a", depth=2, direction="DOWN")
    assert result["root_id"] == "node-a"
    assert result["visited_count"] >= 2
    # 第一层应有 node-b
    ids = [n["id"] for n in result["tree"][0]["children"]]
    assert "node-b" in ids


def test_inspect_lineage_up(populated_db: str) -> None:
    """lineage UP 找到父节点。"""
    result = inspect_lineage(populated_db, "node-b", depth=2, direction="UP")
    parents = result["tree"][0]["parents"]
    parent_ids = [p["id"] for p in parents]
    assert "node-a" in parent_ids


def test_inspect_lineage_both(populated_db: str) -> None:
    """lineage BOTH 同时走两个方向。"""
    result = inspect_lineage(populated_db, "node-b", depth=2, direction="BOTH")
    # 应同时有 parents 和 children 字段
    assert "parents" in result["tree"][0]
    assert "children" in result["tree"][0]


def test_inspect_lineage_empty_tree(populated_db: str) -> None:
    """root 节点被列出但 children/parents 为空。"""
    result = inspect_lineage(populated_db, "node-c", depth=2, direction="UP")
    # node-c 是 leaf，没有 parents
    assert len(result["tree"]) == 1
    assert result["tree"][0]["id"] == "node-c"
    assert result["tree"][0]["parents"] == []
    assert result["tree"][0]["children"] == []


# ============================================================================
# inspect_metrics
# ============================================================================


def test_inspect_metrics_returns_counts(populated_db: str) -> None:
    """metrics 返回总梯度数 + token cost。"""
    m = inspect_metrics(populated_db)
    assert m["total_gradients"] == 1
    assert m["token_cost_total"] == 5
    assert m["by_source"]["USER"] == 1


def test_inspect_metrics_recent_gradients(populated_db: str) -> None:
    """recent_gradients 列表。"""
    m = inspect_metrics(populated_db)
    assert len(m["recent_gradients"]) == 1
    assert m["recent_gradients"][0]["llm_used"] == "static"


def test_inspect_metrics_with_since(populated_db: str) -> None:
    """since 过滤生效。"""
    # since 在 gradient 之前 → 0 个
    m = inspect_metrics(populated_db, since=200.0)
    assert m["total_gradients"] == 0


def test_inspect_metrics_no_table_returns_message(tmp_path: Path) -> None:
    """表不存在时返回 message。"""
    db_path = tmp_path / "empty.sqlite"
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.close()
    m = inspect_metrics(str(db_path))
    assert m["total_gradients"] == 0
    assert "message" in m


# ============================================================================
# inspect_versions
# ============================================================================


def test_inspect_versions_returns_list(populated_db: str) -> None:
    """versions 返回快照列表。"""
    v = inspect_versions(populated_db)
    assert v["count"] == 1
    assert v["snapshots"][0]["version_id"] == "snap-1"
    assert v["snapshots"][0]["tag"] == "v0.1"


def test_inspect_versions_empty_db(tmp_path: Path) -> None:
    """空 DB 返回 message。"""
    db_path = tmp_path / "empty.sqlite"
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.close()
    v = inspect_versions(str(db_path))
    assert v["snapshots"] == []


# ============================================================================
# 文本 / JSON 格式化
# ============================================================================


def test_to_text_dag(populated_db: str) -> None:
    """to_text 输出 DAG 概况文本。"""
    d = inspect_dag(populated_db)
    text = to_text(d)
    assert "hermetic-dmsa DAG Inspection" in text
    assert "Nodes:" in text
    assert "Edges:" in text
    assert "TEXT" in text


def test_to_text_metrics(populated_db: str) -> None:
    """to_text 输出 metrics 文本。"""
    m = inspect_metrics(populated_db)
    text = to_text(m)
    assert "Backward Gradient Metrics" in text
    assert "Total gradients:" in text


def test_to_text_node(populated_db: str) -> None:
    """to_text 输出 node 详情文本。"""
    n = inspect_node(populated_db, "node-a")
    text = to_text(n)
    assert "Node Inspection" in text
    assert "node-a" in text
    assert "first memory" in text


def test_to_text_lineage(populated_db: str) -> None:
    """to_text 输出 lineage 树文本。"""
    l = inspect_lineage(populated_db, "node-a", depth=2, direction="DOWN")
    text = to_text(l)
    assert "Lineage Inspection" in text
    assert "node-a" in text


def test_to_text_versions(populated_db: str) -> None:
    """to_text 输出 versions 文本。"""
    v = inspect_versions(populated_db)
    text = to_text(v)
    assert "Version Snapshots" in text
    assert "snap-1" in text


def test_to_json(populated_db: str) -> None:
    """to_json 输出合法 JSON。"""
    d = inspect_dag(populated_db)
    text = to_json(d)
    parsed = json.loads(text)
    assert parsed["node_count"] == 3


# ============================================================================
# CLI 入口
# ============================================================================


def test_cli_dag(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI dag 子命令。"""
    rc = inspect_main(["--storage", populated_db, "dag"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "DAG Inspection" in captured.out


def test_cli_dag_json(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI dag --json。"""
    rc = inspect_main(["--storage", populated_db, "--json", "dag"])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["node_count"] == 3


def test_cli_node(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI node 子命令。"""
    rc = inspect_main(["--storage", populated_db, "node", "--node-id", "node-a"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "node-a" in captured.out


def test_cli_node_not_found(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI node 不存在节点 → 退出码 1。"""
    rc = inspect_main(["--storage", populated_db, "node", "--node-id", "nonexistent"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()


def test_cli_lineage(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI lineage 子命令。"""
    rc = inspect_main(
        ["--storage", populated_db, "lineage", "--node-id", "node-a", "--depth", "2"]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Lineage" in captured.out


def test_cli_metrics(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI metrics 子命令。"""
    rc = inspect_main(["--storage", populated_db, "metrics"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Backward Gradient" in captured.out


def test_cli_versions(populated_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI versions 子命令。"""
    rc = inspect_main(["--storage", populated_db, "versions"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Version Snapshots" in captured.out


def test_cli_missing_command(populated_db: str) -> None:
    """缺子命令返回 1。"""
    with pytest.raises(SystemExit):
        inspect_main(["--storage", populated_db])


def test_cli_missing_storage() -> None:
    """缺 --storage 返回 1。"""
    with pytest.raises(SystemExit):
        inspect_main(["dag"])


# 抑制 unused import warning
_ = (MemoryLayer, create_prompt_context, HashEmbedder)