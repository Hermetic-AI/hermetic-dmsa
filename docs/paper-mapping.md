# Paper Mapping — 论文对应关系

> hermetic-dmsa 的每个模块对应论文哪一章 / 哪个创新点。

## 论文 4 大创新点 vs hermetic-dmsa

| 论文创新点 | 论文章节 | hermetic-dmsa 模块 |
|---|---|---|
| **1. Provenance-aware Memory DAG** | 论文 §3 | `dmsa.memory` (4 个文件) |
| **2. 可微 Memory Schema（requires_grad）** | 论文 §4 | `dmsa.memory.layer` + `dmsa.types` |
| **3. SGA 文本梯度聚合** | 论文 §5 | `dmsa.gradient` (6 个文件) |
| **4. Tool-Use 反向传播** | 论文 §6 | `dmsa.toolbp` (5 个文件) |

## 模块 ↔ 论文详细映射

### L1 记忆层（论文 §3）

| 文件 | 对应论文 |
|---|---|
| `src/dmsa/memory/layer.py` | §3.1 MemoryNode 设计；§3.2 DAG 操作；§3.3 creator chain |
| `src/dmsa/memory/prompt_context.py` | §3.1 PromptContext 字段；§3.4 G1 验证 |
| `src/dmsa/memory/lineage_query.py` | §3.5 lineage 检索 API |
| `src/dmsa/memory/serialization.py` | §3.6 序列化 |

### L2 梯度聚合（论文 §5 SGA）

| 文件 | 对应论文 |
|---|---|
| `src/dmsa/gradient/samples.py` | §5.1 GradientSample 数据结构 |
| `src/dmsa/gradient/embedding.py` | §5.2 嵌入编码（v1 用哈希 fallback，v2 用 sentence-transformer） |
| `src/dmsa/gradient/llm.py` | §5.2 真实嵌入（OpenAIEmbedder）+ §5.4 真实 LLM 融合（LLMFusion，可选依赖 openai） |
| `src/dmsa/gradient/clustering.py` | §5.3 HDBSCAN 密度聚类 + 单链接 fallback |
| `src/dmsa/gradient/fusion.py` | §5.4 LLM 融合（v1 StaticFusion / RuleBasedFusion，v2 接真实 LLM） |
| `src/dmsa/gradient/routing.py` | §5.5 冲突检测 + 条件分支生成 |
| `src/dmsa/gradient/aggregator.py` | §5.6 SGAAggregator 完整流程 |

### L3 Tool-Use 反向传播（论文 §6）

| 文件 | 对应论文 |
|---|---|
| `src/dmsa/toolbp/schema_node.py` | §6.1 ToolSchemaNode 设计 |
| `src/dmsa/toolbp/ast_projection.py` | §6.2 JSON Schema ↔ AST 投影 |
| `src/dmsa/toolbp/edit_ops.py` | §6.3 编辑指令类型 (UPDATE_LEAF / UPDATE_REQUIRED) |
| `src/dmsa/toolbp/critic.py` | §6.4 工具调用结果 → FeedbackSignal 启发式 |
| `src/dmsa/toolbp/projector.py` | §6.5 ToolBPEngine 编排 + AST 投影 + schema 校验 |

### L4 MCP Server（论文 §7 系统集成）

| 文件 | 对应论文 |
|---|---|
| `src/dmsa/mcp_server/protocol.py` | §7.1 MCP 协议 |
| `src/dmsa/mcp_server/server.py` | §7.2 server 主体 |
| `src/dmsa/mcp_server/tools.py` | §7.3 7 个工具定义 |
| `src/dmsa/mcp_server/handlers/*.py` | §7.4 工具实现 |
| `src/dmsa/mcp_server/cli.py` | §7.5 dmsa-mcp-server CLI 入口 |

## 实验数据来源

MemoryLayer 生命周期过滤的实现说明见
[active-memory-retrieval](algorithms/active-memory-retrieval.md)。它保留 PMD 原有评分，
仅在候选和 lineage 扩展结果进入排序前剔除非 ACTIVE/已过期节点，不改变论文算法结论。

论文中的所有 benchmark 数据都可通过 `dmsa` 公共 API 复现：

```python
# 写入测试记忆
layer.add_memory(content=..., prompt_context=ctx, importance=0.8)

# 反馈
samples = [make_sample(feedback_text=...) for _ in range(N)]
result = SGAAggregator().aggregate(samples)
```

详见 `examples/` 目录的 6 个可运行示例。
