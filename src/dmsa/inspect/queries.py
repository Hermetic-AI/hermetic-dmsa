"""只读查询：DAG 概况、节点详情、lineage 树与版本快照。

依赖规则：只 import stdlib + dmsa.errors + 同层 inspect.db_access。
"""

from __future__ import annotations

import json
from typing import Any

from dmsa.errors import LineageNotFoundError
from dmsa.inspect.db_access import _rows_as_dicts, open_readonly_db

__all__ = [
    "inspect_dag",
    "inspect_lineage",
    "inspect_node",
    "inspect_versions",
]


def inspect_dag(path: str) -> dict[str, Any]:
    """扫描 DAG 概况。

    Returns:
        dict 含：
        - node_count: 总节点数
        - edge_count: 总边数
        - node_types: {TEXT: N, EMBEDDING: M, ...}
        - status_counts: {ACTIVE: N, TOMBSTONED: M, ...}
        - top_tags: [(tag, count), ...] 前 10
        - creator_agents: [(agent_id, count), ...] 前 10
        - first_node_at / last_node_at: 时间戳
    """
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        # 总数
        cursor.execute("SELECT COUNT(*) FROM memory_nodes")
        node_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM creator_edges")
        edge_count = cursor.fetchone()[0]
        # 类型分布
        cursor.execute("SELECT content_type, COUNT(*) FROM memory_nodes GROUP BY content_type")
        node_types = {row[0]: row[1] for row in cursor.fetchall()}
        # 状态分布
        cursor.execute("SELECT status, COUNT(*) FROM memory_nodes GROUP BY status")
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}
        # top tags
        cursor.execute("SELECT tags FROM memory_nodes WHERE tags != '[]'")
        tag_counter: dict[str, int] = {}
        for (tags_json,) in cursor.fetchall():
            try:
                tags = json.loads(tags_json)
            except (TypeError, ValueError):
                continue
            for tag in tags:
                tag_counter[tag] = tag_counter.get(tag, 0) + 1
        top_tags = sorted(tag_counter.items(), key=lambda kv: -kv[1])[:10]
        # creator agents
        cursor.execute(
            "SELECT creator_agent_id, COUNT(*) FROM memory_nodes "
            "GROUP BY creator_agent_id ORDER BY COUNT(*) DESC LIMIT 10"
        )
        creator_agents = [(row[0], row[1]) for row in cursor.fetchall()]
        # 时间范围
        cursor.execute("SELECT MIN(created_at), MAX(created_at) FROM memory_nodes")
        row = cursor.fetchone()
        first_at, last_at = (row[0], row[1]) if row else (None, None)

        return {
            "schema": "dmsa.inspect.v1",
            "storage_path": path,
            "node_count": node_count,
            "edge_count": edge_count,
            "node_types": node_types,
            "status_counts": status_counts,
            "top_tags": top_tags,
            "creator_agents": creator_agents,
            "first_node_at": first_at,
            "last_node_at": last_at,
        }
    finally:
        conn.close()


def inspect_node(path: str, node_id: str) -> dict[str, Any]:
    """读取单个节点的完整信息。"""
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if row is None:
            raise LineageNotFoundError(f"node {node_id!r} not found", details={"node_id": node_id})
        cols = [d[0] for d in cursor.description]
        node = dict(zip(cols, row, strict=True))
        # creator context
        if node.get("creator_prompt_id"):
            cursor.execute(
                "SELECT * FROM prompt_contexts WHERE prompt_id = ?",
                (node["creator_prompt_id"],),
            )
            prompt_row = cursor.fetchone()
            if prompt_row:
                prompt_cols = [d[0] for d in cursor.description]
                node["creator_prompt"] = dict(zip(prompt_cols, prompt_row, strict=True))
        # parents / children
        cursor.execute("SELECT * FROM creator_edges WHERE to_node_id = ?", (node_id,))
        node["parents_edges"] = _rows_as_dicts(cursor)
        cursor.execute("SELECT * FROM creator_edges WHERE from_node_id = ?", (node_id,))
        node["children_edges"] = _rows_as_dicts(cursor)
        # accumulated feedback
        cursor.execute(
            "SELECT * FROM backward_gradients "
            "WHERE ancestor_node_id = ? OR descendant_node_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (node_id, node_id),
        )
        node["recent_gradients"] = _rows_as_dicts(cursor)
        return node
    finally:
        conn.close()


def inspect_lineage(
    path: str, node_id: str, *, depth: int = 3, direction: str = "BOTH"
) -> dict[str, Any]:
    """递归遍历 lineage，返回树形结构。"""
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        visited: set[str] = set()

        def walk(nid: str, current_depth: int, going: str) -> dict[str, Any] | None:
            """返回单个节点 dict 或 None。"""
            if current_depth <= 0 or nid in visited:
                return None
            visited.add(nid)
            cursor.execute(
                "SELECT content, status, importance FROM memory_nodes WHERE id = ?",
                (nid,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            content, status, importance = row
            # children
            children: list[dict[str, Any]] = []
            if going in ("DOWN", "BOTH"):
                cursor.execute(
                    "SELECT to_node_id FROM creator_edges WHERE from_node_id = ?",
                    (nid,),
                )
                for (child_id,) in cursor.fetchall():
                    child = walk(child_id, current_depth - 1, "DOWN")
                    if child is not None:
                        children.append(child)
            # parents
            parents: list[dict[str, Any]] = []
            if going in ("UP", "BOTH"):
                cursor.execute(
                    "SELECT from_node_id FROM creator_edges WHERE to_node_id = ?",
                    (nid,),
                )
                for (parent_id,) in cursor.fetchall():
                    parent = walk(parent_id, current_depth - 1, "UP")
                    if parent is not None:
                        parents.append(parent)
            return {
                "id": nid,
                "content_preview": content[:60] if content else "",
                "status": status,
                "importance": importance,
                "children": children,
                "parents": parents,
            }

        root_node = walk(node_id, depth, direction)
        tree = [root_node] if root_node is not None else []
        return {
            "schema": "dmsa.inspect.lineage.v1",
            "root_id": node_id,
            "depth": depth,
            "direction": direction,
            "tree": tree,
            "visited_count": len(visited),
        }
    finally:
        conn.close()


def inspect_versions(path: str) -> dict[str, Any]:
    """列出所有版本快照。"""
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='version_snapshots'"
        )
        if cursor.fetchone() is None:
            return {
                "schema": "dmsa.inspect.versions.v1",
                "snapshots": [],
                "message": "version_snapshots table not found",
            }
        cursor.execute(
            "SELECT version_id, parent_version_id, created_at, description, tag, "
            "node_count, edge_count, content_hash FROM version_snapshots "
            "ORDER BY created_at DESC"
        )
        snapshots = _rows_as_dicts(cursor)
        return {
            "schema": "dmsa.inspect.versions.v1",
            "snapshots": snapshots,
            "count": len(snapshots),
        }
    finally:
        conn.close()
