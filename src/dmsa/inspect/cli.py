"""Inspect CLI 实现（``dmsa-inspect`` 命令）。

只读诊断：扫描一个 SQLite 数据库或内存 DAG，输出可读的文本或 JSON。
零依赖（用 stdlib ``sqlite3`` + ``json``）。

依赖规则：只 import stdlib + dmsa.errors。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from dmsa.errors import LineageNotFoundError, MemoryLayerError

__all__ = [
    "inspect_dag",
    "inspect_node",
    "inspect_lineage",
    "inspect_metrics",
    "inspect_versions",
    "open_readonly_db",
    "to_text",
    "to_json",
]


# ============================================================================
# 数据库访问（直接读 SQLite，不依赖 pmd）
# ============================================================================


def open_readonly_db(path: str) -> sqlite3.Connection:
    """打开 SQLite 数据库（只读模式）。

    Args:
        path: SQLite 文件路径。``:memory:`` 不可用（只读模式不支持）。

    Returns:
        :class:`sqlite3.Connection`。

    Raises:
        MemoryLayerError: 数据库无法打开或表结构不匹配。
    """
    if path == ":memory:":
        raise MemoryLayerError(
            "cannot inspect :memory: database (use a file path)",
            details={"hint": "use --storage ~/.hermetic-dmsa/db.sqlite"},
        )
    if not path or path.startswith("~"):
        path = _expand_user(path)
    try:
        # uri 格式必须用 file: 前缀
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise MemoryLayerError(
            f"failed to open database: {exc}",
            details={"path": path, "error": str(exc)},
        ) from exc
    return conn


def _expand_user(path: str) -> str:
    """展开 ``~`` 到用户主目录。"""
    import os

    return os.path.expanduser(path)


# ============================================================================
# Inspect 命令
# ============================================================================


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
            raise LineageNotFoundError(
                f"node {node_id!r} not found", details={"node_id": node_id}
            )
        cols = [d[0] for d in cursor.description]
        node = dict(zip(cols, row))
        # creator context
        if node.get("creator_prompt_id"):
            cursor.execute(
                "SELECT * FROM prompt_contexts WHERE prompt_id = ?",
                (node["creator_prompt_id"],),
            )
            prompt_row = cursor.fetchone()
            if prompt_row:
                prompt_cols = [d[0] for d in cursor.description]
                node["creator_prompt"] = dict(zip(prompt_cols, prompt_row))
        # parents / children
        cursor.execute(
            "SELECT * FROM creator_edges WHERE to_node_id = ?", (node_id,)
        )
        node["parents_edges"] = [dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()]
        cursor.execute(
            "SELECT * FROM creator_edges WHERE from_node_id = ?", (node_id,)
        )
        node["children_edges"] = [dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()]
        # accumulated feedback
        cursor.execute(
            "SELECT * FROM backward_gradients WHERE ancestor_node_id = ? OR descendant_node_id = ? ORDER BY created_at DESC LIMIT 20",
            (node_id, node_id),
        )
        node["recent_gradients"] = [
            dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()
        ]
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


def inspect_metrics(path: str, *, since: float | None = None) -> dict[str, Any]:
    """导出 backward_gradients 表的统计 + 最近记录。

    Args:
        path: SQLite 路径。
        since: Unix 时间戳；只统计该时间之后的记录（None = 全部）。

    Returns:
        dict 含：
        - total_gradients: 总数
        - recent_gradients: 最近 20 条
        - by_source: {FeedbackSource: count}
        - token_cost_total: 总 token 消耗
    """
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        # 检查表是否存在
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backward_gradients'"
        )
        if cursor.fetchone() is None:
            return {
                "schema": "dmsa.inspect.metrics.v1",
                "total_gradients": 0,
                "recent_gradients": [],
                "by_source": {},
                "token_cost_total": 0,
                "message": "backward_gradients table not found (no backward pass yet)",
            }
        where = "WHERE created_at >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        cursor.execute(f"SELECT COUNT(*) FROM backward_gradients {where}", params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT * FROM backward_gradients {where} ORDER BY created_at DESC LIMIT 20",
            params,
        )
        cols = [d[0] for d in cursor.description]
        recent = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.execute(f"SELECT feedback_source, COUNT(*) FROM backward_gradients {where} GROUP BY feedback_source", params)
        by_source = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(f"SELECT COALESCE(SUM(token_cost), 0) FROM backward_gradients {where}", params)
        token_total = cursor.fetchone()[0] or 0
        return {
            "schema": "dmsa.inspect.metrics.v1",
            "since": since,
            "total_gradients": total,
            "recent_gradients": recent,
            "by_source": by_source,
            "token_cost_total": token_total,
        }
    finally:
        conn.close()


def inspect_versions(path: str) -> dict[str, Any]:
    """列出所有版本快照。"""
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='version_snapshots'")
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
        cols = [d[0] for d in cursor.description]
        snapshots = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return {
            "schema": "dmsa.inspect.versions.v1",
            "snapshots": snapshots,
            "count": len(snapshots),
        }
    finally:
        conn.close()


# ============================================================================
# 输出格式化
# ============================================================================


def to_text(payload: dict[str, Any]) -> str:
    """把 inspect dict 渲染为人类可读文本。"""
    schema = payload.get("schema", "")
    if schema == "dmsa.inspect.v1":
        return _text_dag(payload)
    if schema == "dmsa.inspect.lineage.v1":
        return _text_lineage(payload)
    if schema == "dmsa.inspect.metrics.v1":
        return _text_metrics(payload)
    if schema == "dmsa.inspect.versions.v1":
        return _text_versions(payload)
    # 节点详情（无 schema 字段）
    return _text_node(payload)


def _text_dag(d: dict[str, Any]) -> str:
    """DAG 概况文本。"""
    lines = [
        "=" * 60,
        "  hermetic-dmsa DAG Inspection",
        "=" * 60,
        f"  Storage: {d.get('storage_path')}",
        f"  Nodes:   {d.get('node_count', 0)}",
        f"  Edges:   {d.get('edge_count', 0)}",
        "",
        "  -- Node Types --",
    ]
    for k, v in sorted(d.get("node_types", {}).items()):
        lines.append(f"    {k:20s} {v:>6}")
    lines += ["", "  -- Status --"]
    for k, v in sorted(d.get("status_counts", {}).items()):
        lines.append(f"    {k:20s} {v:>6}")
    lines += ["", "  -- Top Tags --"]
    for tag, count in d.get("top_tags", []):
        lines.append(f"    {tag:30s} {count:>6}")
    lines += ["", "  -- Top Creator Agents --"]
    for agent_id, count in d.get("creator_agents", []):
        lines.append(f"    {agent_id:30s} {count:>6}")
    if d.get("first_node_at") and d.get("last_node_at"):
        import datetime

        first = datetime.datetime.fromtimestamp(d["first_node_at"]).isoformat()
        last = datetime.datetime.fromtimestamp(d["last_node_at"]).isoformat()
        lines += ["", f"  First node: {first}", f"  Last node:  {last}"]
    lines.append("=" * 60)
    return "\n".join(lines)


def _text_lineage(d: dict[str, Any]) -> str:
    """Lineage 树文本。"""
    lines = [
        "=" * 60,
        "  hermetic-dmsa Lineage Inspection",
        "=" * 60,
        f"  Root:     {d['root_id']}",
        f"  Depth:    {d['depth']}",
        f"  Direction: {d['direction']}",
        f"  Visited:  {d['visited_count']}",
        "",
    ]

    def render_node(node: dict[str, Any], indent: int) -> None:
        prefix = "  " * indent + "- "
        lines.append(
            f"{prefix}{node['id'][:20]}..."
            f"  [{node['status']} imp={node['importance']:.2f}]"
            f"  {node['content_preview']}"
        )
        for child in node.get("children", []):
            render_node(child, indent + 1)
        for parent in node.get("parents", []):
            render_node(parent, indent + 1)

    if d["tree"]:
        render_node(d["tree"][0], 0)
    else:
        lines.append("  (no lineage found)")
    lines.append("=" * 60)
    return "\n".join(lines)


def _text_metrics(d: dict[str, Any]) -> str:
    """Metrics 文本。"""
    lines = [
        "=" * 60,
        "  hermetic-dmsa Backward Gradient Metrics",
        "=" * 60,
        f"  Total gradients:  {d['total_gradients']}",
        f"  Token cost total: {d['token_cost_total']}",
    ]
    if d.get("since"):
        import datetime

        lines.append(
            f"  Since:             {datetime.datetime.fromtimestamp(d['since']).isoformat()}"
        )
    lines += ["", "  -- By Feedback Source --"]
    for src, cnt in sorted(d.get("by_source", {}).items()):
        lines.append(f"    {src:20s} {cnt:>6}")
    lines += ["", "  -- Recent Gradients (last 20) --"]
    for g in d.get("recent_gradients", [])[:20]:
        preview = (g.get("gradient_text") or "")[:60].replace("\n", " ")
        lines.append(
            f"    [{g.get('created_at', 0):.0f}] "
            f"{g.get('feedback_source', '?'):12s} "
            f"tokens={g.get('token_cost', 0):>4}  {preview}"
        )
    if "message" in d:
        lines += ["", f"  Note: {d['message']}"]
    lines.append("=" * 60)
    return "\n".join(lines)


def _text_versions(d: dict[str, Any]) -> str:
    """Versions 文本。"""
    lines = [
        "=" * 60,
        "  hermetic-dmsa Version Snapshots",
        "=" * 60,
    ]
    if "message" in d:
        lines += [f"  Note: {d['message']}", "=" * 60]
        return "\n".join(lines)
    lines.append(f"  Total: {d.get('count', 0)}")
    for s in d.get("snapshots", []):
        import datetime

        ts = datetime.datetime.fromtimestamp(s.get("created_at", 0)).isoformat()
        tag = f" [{s['tag']}]" if s.get("tag") else ""
        lines.append(
            f"  {s['version_id'][:20]}...{tag}  "
            f"{ts}  nodes={s.get('node_count', 0)} edges={s.get('edge_count', 0)}"
        )
        if s.get("description"):
            lines.append(f"      {s['description']}")
    lines.append("=" * 60)
    return "\n".join(lines)


def _text_node(d: dict[str, Any]) -> str:
    """节点详情文本。"""
    lines = [
        "=" * 60,
        "  hermetic-dmsa Node Inspection",
        "=" * 60,
        f"  ID:            {d.get('id')}",
        f"  Version:       {d.get('version')}",
        f"  Content type:  {d.get('content_type')}",
        f"  Status:        {d.get('status')}",
        f"  Importance:    {d.get('importance')}",
        f"  Tags:          {d.get('tags')}",
        f"  requires_grad: {d.get('requires_grad')}",
        "",
        "  Content:",
        f"    {(d.get('content') or '')[:200]}",
        "",
        f"  Creator prompt_id: {d.get('creator_prompt_id')}",
        f"  Creator agent_id:  {d.get('creator_agent_id')}",
    ]
    if d.get("creator_prompt"):
        cp = d["creator_prompt"]
        lines += [
            "",
            "  Creator PromptContext:",
            f"    model:        {cp.get('model_name')}",
            f"    temperature:  {cp.get('temperature')}",
            f"    system_prompt: {(cp.get('system_prompt') or '')[:80]}",
            f"    user_prompt:   {(cp.get('user_prompt') or '')[:80]}",
        ]
    lines += [
        "",
        f"  Parents edges:   {len(d.get('parents_edges', []))}",
        f"  Children edges:  {len(d.get('children_edges', []))}",
        f"  Recent gradients: {len(d.get('recent_gradients', []))}",
    ]
    if d.get("recent_gradients"):
        lines += ["", "  Recent gradients:"]
        for g in d["recent_gradients"][:5]:
            preview = (g.get("gradient_text") or "")[:80].replace("\n", " ")
            lines.append(f"    - [{g.get('feedback_source', '?')}] {preview}")
    lines.append("=" * 60)
    return "\n".join(lines)


def to_json(payload: dict[str, Any]) -> str:
    """把 inspect dict 序列化为 JSON。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)