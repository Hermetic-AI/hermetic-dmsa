# Paper Mapping — 论文对应关系

`gradient/integrated.py`、`gradient/feedback.py`：检索策略内循环的运行时集成，
将显式反馈、模型受限提案、固定回放门禁、版本启用及回滚连接到 CodingMemory。
该机制验证检索召回代理指标，不构成代码任务成功率的实证结论。

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

Coding Memory 的 `dmsa.memory.coding.experiences` 是面向开源工程记忆赛道的研究原型，
不属于上述论文已验证的 DAG/SGA 创新点。它从切片原文形成保守的 facet 视图并作轻量检索先验；
其效果须由代理噪声基准和真实 Coding Agent 配对任务测量，不应作为论文结果引用。
同层的 `coding.tokenize` 与 `coding.storage_queries` 稀疏通道（trigram 分词 + 标识符片段拆分 + 短词 LIKE 兜底）
是可检索性工程修正，同样不对应论文创新点，只影响召回通道的实现细节。
`coding.ranking` 的证据质量先验（目标路径补丁、已验证代码、失败尝试与过期写入降权）也是工程排序
修正，不声称论文实验结果或 coding 成功率；其效果只能由代理噪声基准和真实 Coding Agent 配对任务测量。

### L2 梯度聚合（论文 §5 SGA）

| `src/dmsa/gradient/policy.py`, `replay.py`, `model_advisor.py` | 离线检索候选与配对门禁；可选模型仅做候选选择/解释，属于工程评测闭环，不声称论文实验结果或 coding 成功率 |

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
