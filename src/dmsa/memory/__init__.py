"""L1 记忆层(封装 PMD skeleton).

提供对 pmd.ProvenanceDAG 的薄封装:

- :class:`MemoryLayer` — 主入口
- :class:`LineageQuery` — 构造 lineage 查询
- :func:`create_prompt_context` — PromptContext 工厂
- :func:`to_dict` / :func:`from_dict` — JSON 序列化

所有依赖规则: 本层只 import ``pmd``, 不 import L2/L3/L4.
"""

from __future__ import annotations

from dmsa.memory.coding import (
    AsOf,
    CodingMemory,
    CodingMemoryConflictError,
    CodingMemoryError,
    CodingMemoryValidationError,
    ConflictResolution,
    ConsolidationRecord,
    ConsolidationReport,
    CorpusState,
    DenseScopeTooLargeError,
    Generalization,
    HybridItem,
    HybridPolicy,
    HybridResult,
    Procedure,
    ProcedureMatch,
    QdrantVectorIndex,
    RecallExplanation,
    RecallItem,
    RecallRound,
    RuleGroup,
    SearchHit,
    SearchPolicy,
    SourceMessage,
    TemporalResult,
    TransferRecord,
    TransferReport,
    VectorIndex,
    WriteConflict,
    apply_verdict,
    ensure_stats_table,
    estimate_tokens,
    scope_key,
    team_scope,
)
from dmsa.memory.layer import MemoryLayer
from dmsa.memory.lineage_query import LineageDirection, LineageQuery
from dmsa.memory.prompt_context import (
    PromptContext,
    create_prompt_context,
)
from dmsa.memory.serialization import from_dict, to_dict

__all__ = [
    "AsOf",
    "CodingMemory",
    "CodingMemoryConflictError",
    "CodingMemoryError",
    "CodingMemoryValidationError",
    "ConflictResolution",
    "ConsolidationRecord",
    "ConsolidationReport",
    "CorpusState",
    "DenseScopeTooLargeError",
    "Generalization",
    "HybridItem",
    "HybridPolicy",
    "HybridResult",
    "LineageDirection",
    "LineageQuery",
    "MemoryLayer",
    "Procedure",
    "ProcedureMatch",
    "PromptContext",
    "QdrantVectorIndex",
    "RecallExplanation",
    "RecallItem",
    "RecallRound",
    "RuleGroup",
    "SearchHit",
    "SearchPolicy",
    "SourceMessage",
    "TemporalResult",
    "TransferRecord",
    "TransferReport",
    "VectorIndex",
    "WriteConflict",
    "apply_verdict",
    "create_prompt_context",
    "ensure_stats_table",
    "estimate_tokens",
    "from_dict",
    "scope_key",
    "team_scope",
    "to_dict",
]
