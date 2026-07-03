---
name: hermetic-dmsa
description: |
  可微记忆层 — 让 Hermes Agent 拥有 provenance-aware memory、文本反向传播、
  SGA 梯度聚合、Tool-Use 反向传播能力。零依赖，默认静态 backward deriver。

  当用户要求 Hermes 持久化记忆、追踪工具调用 lineage、基于反馈改进 prompt、
  或审计记忆来源时使用本 skill。
---

# hermetic-dmsa Skill

## 何时调用

- 用户说"记一下这个"、"以后记住..."、"我偏好 X" — 触发 `dmsa_add_memory`
- 用户反馈某个回答不准确 / 需要调整 — 触发 `dmsa_feedback` + `dmsa_apply_gradient`
- 用户问"我之前说过什么"、"你记得 X 吗" — 触发 `dmsa_search` / `dmsa_query_lineage`
- 工具调用失败 — 触发 `dmsa_tool_call` 记录 + 自动生成 critique
- 用户要回滚到之前状态 — 触发 `dmsa_snapshot` / `pmd.rollback`

## 7 个工具速查

| 工具 | 何时用 |
|---|---|
| `dmsa_add_memory` | 写入一条新记忆（含 creator 链） |
| `dmsa_query_lineage` | 查询某节点的祖先/后代链 |
| `dmsa_feedback` | 用户反馈 → 触发 SGA 聚合 |
| `dmsa_apply_gradient` | 把聚合后的梯度应用到 prompt 或 schema |
| `dmsa_tool_call` | 包装工具调用 + 自动生成 critique |
| `dmsa_snapshot` | 打点版本（可回滚） |
| `dmsa_search` | lineage-aware 检索 |

## 调用方式

Hermes 通过 stdio MCP 协议与 hermetic-dmsa server 通信。server 启动命令：

```bash
dmsa-mcp-server --storage ~/.hermetic-dmsa/hermes.sqlite
```

## 安装

```bash
pip install -e ../analysis/pmd_skeleton  # PMD 库
pip install -e .                          # hermetic-dmsa 本体
```

## 典型工作流

### 1. 写入记忆

```python
mcp call dmsa_add_memory \
  --content "user prefers dark mode" \
  --system_prompt "you are a UI assistant" \
  --user_prompt "remember I like dark mode" \
  --tags '["user_pref"]' \
  --importance 0.9
```

返回的 `node.id` 是新记忆的 ULID。

### 2. 检索

```python
mcp call dmsa_search --query "dark mode preference" --k 5
```

### 3. 反馈 + 反向传播

```python
mcp call dmsa_feedback \
  --target_node_id "<上一步的 node.id>" \
  --feedback_text "回答太简略了，应该详细一点" \
  --source USER \
  --severity 0.8
```

返回的 `fused_gradients[0].fused_text` 可用于更新 prompt。

### 4. 工具调用失败 → 自动 critique

```python
mcp call dmsa_tool_call \
  --tool_name get_date \
  --args '{"format": "wrong"}' \
  --error "format must be YYYY-MM-DD"
```

返回的 `critique.category = "schema"` 提示是 schema 描述模糊，下一轮可触发 `dmsa_apply_gradient` 改进 schema。

## 配置

通过环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DMSA_STORAGE__PATH` | `:memory:` | SQLite 存储路径 |
| `DMSA_EMBEDDER__DIM` | `256` | 哈希嵌入维度 |
| `DMSA_GRADIENT__CLUSTERING_BACKEND` | `fallback` | `fallback` 或 `hdbscan` |
| `DMSA_GRADIENT__CONFLICT_THRESHOLD` | `0.3` | SGA 冲突检测阈值 |
| `DMSA_MCP_SERVER__MAX_FEEDBACK_LENGTH` | `100000` | 单条 feedback 最大字符数 |

## 详细文档

参见 [docs/integrations.md](../../docs/integrations.md) 和论文对应章节。
