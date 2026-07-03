# Architecture

> hermetic-dmsa 的 5 层架构图与数据流。

## 5 层架构

```
┌────────────────────────────────────────────────────────────┐
│ L5  Integrations（用户可见的接入点）                        │
│     - Claude Code (.mcp.json)                              │
│     - OpenCode (opencode.json)                             │
│     - Hermes Agent (SKILL.md)                              │
└────────────────────────────────────────────────────────────┘
                            │ stdio JSON-RPC 2.0
┌────────────────────────────────────────────────────────────┐
│ L4  MCP Server (dmsa.mcp_server)                           │
│     - 7 个工具暴露给 Agent                                  │
│     - 协议：JSON-RPC 2.0 + MCP 2025-06-18                  │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│ L3  Tool-Use Backpropagation (dmsa.toolbp)                 │
│     - JSON Schema AST 投影 + 编辑指令                       │
│     - ToolBPEngine 把 feedback 翻译成 schema update        │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│ L2  Semantic Gradient Aggregation (dmsa.gradient)          │
│     - 零依赖哈希嵌入（HDBSCAN 可选）                        │
│     - 单链接聚类 + 静态融合 + 条件分支生成                   │
│     - SGAAggregator 编排完整流程                            │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│ L1  Memory Layer (dmsa.memory)                             │
│     - 封装 pmd.ProvenanceDAG                                │
│     - 暴露 add_memory / query_lineage / snapshot / search   │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│ L0  pmd (analysis/pmd_skeleton — Provenance-aware Memory DAG)│
└────────────────────────────────────────────────────────────┘
```

## 数据流（端到端）

```
用户反馈 / tool call 失败
    │
    ▼
[1] dmsa_tool_call / dmsa_add_memory  ──→ 写入 PMD DAG (creator chain 完整)
    │
    ▼
[2] dmsa_feedback(target_node_id, feedback_text)
    │  ┌──────────────────────────────────────────────────┐
    │  │ SGAAggregator.aggregate()                        │
    │  │   ├─ 嵌入 (HashEmbedder)                          │
    │  │   ├─ 聚类 (FallbackSingleLinkBackend)             │
    │  │   ├─ 融合 (StaticFusion / RuleBasedFusion)        │
    │  │   ├─ 冲突检测 (ConflictDetector)                  │
    │  │   └─ 条件分支 (ConditionalBranchSynthesizer)      │
    │  └──────────────────────────────────────────────────┘
    │
    ▼
[3] AGSAggregationResult { clusters, fused_gradients, ... }
    │
    ▼
[4] dmsa_apply_gradient(target_type="schema", target_id=tool_name)
    │  ┌──────────────────────────────────────────────────┐
    │  │ ToolBPEngine.project()                           │
    │  │   ├─ parse_schema  → SchemaNode 树              │
    │  │   ├─ feedback → EditOp (UpdateLeafOp / ...)      │
    │  │   ├─ apply_edit_op (copy-on-write)              │
    │  │   └─ 返回新 ToolSchemaNode (version+1)            │
    │  └──────────────────────────────────────────────────┘
    │
    ▼
[5] schema 更新后写入 PMD DAG
    │
    ▼
[6] 下一轮 tool call 成功
```

## 严格依赖规则

- Layer N **只能** import Layer < N
- 不允许反向 import（防止循环依赖）
- L4 (MCP server) 之上 = L5 (Integrations) — 集成层只通过 MCP 协议调用
- L0 (pmd) 是叶子，不依赖任何 dmsa 模块

详见 `AGENTS.md`。
