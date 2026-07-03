"""L1 记忆层（封装 PMD skeleton）。

提供对 pmd.ProvenanceDAG 的薄封装：

- :class:`MemoryLayer` — 主入口
- :class:`LineageQuery` — 构造 lineage 查询
- :func:`create_prompt_context` — PromptContext 工厂
- :func:`to_dict` / :func:`from_dict` — JSON 序列化

所有依赖规则：本层只 import ``pmd``，不 import L2/L3/L4。
"""

from __future__ import annotations

from dmsa.memory.layer import MemoryLayer
from dmsa.memory.lineage_query import LineageQuery, LineageDirection
from dmsa.memory.prompt_context import (
    PromptContext,
    create_prompt_context,
)
from dmsa.memory.serialization import to_dict, from_dict

__all__ = [
    "MemoryLayer",
    "LineageQuery",
    "LineageDirection",
    "PromptContext",
    "create_prompt_context",
    "to_dict",
    "from_dict",
]
