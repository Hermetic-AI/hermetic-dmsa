"""Inspect 子包：只读诊断工具。

提供 CLI 命令 ``dmsa-inspect`` 用于检查 hermetic-dmsa 运行时状态：

- :func:`inspect_dag` — 打印 DAG 概况（节点数 / 边数 / 版本链）
- :func:`inspect_node` — 打印单个节点详情
- :func:`inspect_lineage` — 打印 lineage 树
- :func:`inspect_metrics` — 导出 metrics
- :func:`inspect_versions` — 列出版本快照

只读不修改。不会触发任何反向传播或聚合。
"""

from __future__ import annotations

from dmsa.inspect.cli import (
    inspect_dag,
    inspect_lineage,
    inspect_metrics,
    inspect_node,
    inspect_versions,
)

__all__ = [
    "inspect_dag",
    "inspect_node",
    "inspect_lineage",
    "inspect_metrics",
    "inspect_versions",
]