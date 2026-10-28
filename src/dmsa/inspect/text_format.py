"""只读诊断结果的渲染：人类可读文本与 JSON。

依赖规则：只 import stdlib。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

__all__ = ["to_json", "to_text"]


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
