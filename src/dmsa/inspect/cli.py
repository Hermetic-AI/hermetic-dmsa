"""Inspect CLI 公共入口（``dmsa-inspect`` 命令）。

只读诊断：扫描一个 SQLite 数据库或内存 DAG，输出可读的文本或 JSON。
零依赖（用 stdlib ``sqlite3`` + ``json``）。

实现按职责拆到子模块，本模块只做再导出，保证 ``dmsa.inspect.cli`` 的
对外接口不变：

- :mod:`dmsa.inspect.db_access` — 只读连接与行转换
- :mod:`dmsa.inspect.queries` — DAG / 节点 / lineage / 版本快照查询
- :mod:`dmsa.inspect.metrics_query` — backward_gradients 统计
- :mod:`dmsa.inspect.text_format` — 文本与 JSON 渲染
"""

from __future__ import annotations

from dmsa.inspect.db_access import open_readonly_db
from dmsa.inspect.metrics_query import inspect_metrics
from dmsa.inspect.queries import (
    inspect_dag,
    inspect_lineage,
    inspect_node,
    inspect_versions,
)
from dmsa.inspect.text_format import to_json, to_text

__all__ = [
    "inspect_dag",
    "inspect_lineage",
    "inspect_metrics",
    "inspect_node",
    "inspect_versions",
    "open_readonly_db",
    "to_json",
    "to_text",
]
