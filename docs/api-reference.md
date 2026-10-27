## Coding Memory HTTP API (development subset)

集成模式见 [DMSA + RSI](integrated-rsi.md)。`create_app(memory, api_key, rsi=None)`
可注入 `IntegratedRSI(memory, models=False)`。启用时 `/search` 额外返回 `trace_id`；
认证 `/feedback` 保存标签并自动提案、回放和门禁激活；`/rsi/evolve` 可重试，
`/rsi/rollback` 只接受当前 scope 已保存版本。模型开关不影响固定门禁。

需要完整请求/响应、运行命令与 Agent 测试步骤，请先看 [Coding Memory 实操指南](coding-memory-guide.md)。

`from dmsa.mcp_server.http import create_app` 提供无认证 `GET
/health` 与受认证 `POST /add`、`POST /search`。认证接受 Bearer、Token 或
`X-Api-Key`。调用方注入的 CodingMemory 归调用方管理；HTTP CLI 管理自己的实例。
消息 content 可为字符串或 `{type: "text", text: ...}` parts；只支持纯文本。
搜索由 `user_id` 隔离，`options` 仅接受空数组。`/v1/runs` 尚未实现。

安装 `.[http]` 后 `dmsa-memory-http --profile sparse-dev` 运行离线稀疏开发
模式。`competition` profile 要求显式 endpoint、embedding key 与
`text-embedding-v4` 模型，缺失模型不使用隐式默认值。随附固定 PMD 源码包支持
独立 checkout 和 Docker 部署，见[部署说明](deployment.md)。

CLI 支持 `DMSA_MEMORY_API_KEY_FILE` / `DMSA_EMBEDDING_API_KEY_FILE` 挂载密钥；
非空环境值与对应文件同时提供会抛出 `ConfigError`，CLI 转为不带敏感内容的退出。
`dmsa-memory-smoke` 验证 `/health`、`/add` 与 `/search`，`--search-only` 只验证
相同 request 的已持久化证据，`--health-only` 无需 key 且不写入。

# MemoryLayer API

`from dmsa.memory import MemoryLayer` 的公开导入和方法签名保持稳定。

## 存储配置

`MemoryLayer(config=MemoryLayerConfig(storage_path="path/to/memory.sqlite"))`
创建 SQLite 存储并在新实例中恢复节点和 prompt context。父目录按需创建，
路径使用 `pathlib.Path.expanduser()` 展开。默认 `:memory:` 使用内存存储。
显式 `store=` 始终优先于配置；调用方管理 store 生命周期。
初始化失败抛出 `MemoryLayerError`，其 details 包含配置路径。
CLI 的 `--storage` 和全局配置 `storage.path` 使用同样的接线。

## 检索与更新

- `retrieve(query, k=10)` 与 `lineage_aware_search(query, k=10, depth=2,
  alpha=0.6, beta=0.3, gamma=0.1)` 需要 embedder，否则抛出 `MemoryLayerError`。
- 只有 `status=ACTIVE` 且未过期的节点进入候选集；到期条件为
  `created_at + ttl <= 当前时间`。过滤发生在初始向量候选截断之前，
  lineage 扩展结果也过滤。`k <= 0` 返回空列表。
- 排序保留 PMD 的 cosine/proximity/importance 加权公式和初始 `2*k` 种子策略。
  lineage 的历史拓扑保留，因此不可检索节点仍可作为图路径的一部分。
- `apply_text_gradient(...)` 要求 `requires_grad=True` 且
  `grad_learning_rate > 0`，否则抛出 `MemoryLayerError`，不创建 successor。
- `add_memory(..., parents=[...])` 的缺失父节点错误仍为 `MissingParentError`。

## 快照与序列化

`snapshot` / `list_versions` 继续委托 PMD。SQLite 上的 `rollback` 会抛出
`MemoryLayerError`：当前 PMD rollback 只恢复 live DAG，不能安全回写持久数据库；
可通过 detached export/import 恢复某个内存快照。
`export_state` / `import_state` 保留 `dmsa.memory.v1` 格式。
导入产生独立内存副本，保留 payload 的配置元数据，但不会重新打开并修改其原始数据库。

PMD 从仓库随附的固定 Apache-2.0 源码发行包安装，参见[分发说明](deployment.md)。

## CodingMemory

`from dmsa.memory import CodingMemory, SourceMessage, SearchPolicy` 提供独立于 DAG 的
scope coding evidence API。`CodingMemory(path, embedder=None, policy=None, vector_index=None)` 接受 SQLite
路径；`add(request_id, user_id, session_id, messages)` 幂等保存有序 `SourceMessage`
并返回 scope revision；冲突抛出 `CodingMemoryConflictError`。`search(query, user_id,
top_k, policy=None)` 返回含稳定切片 ID、来源引用文本和 RRF 分数的 `SearchHit`。临时
policy 可用于配对检索，不会修改实例默认策略。`delete_scope(user_id)` 删除 scope 数据，
`corpus_state(user_ids)` 返回同一事务读取的 `CorpusState(revision, fingerprint)`，
`corpus_fingerprint(user_ids)` 为其指纹快捷方式，`close()` 关闭连接。
校验错误抛出 `CodingMemoryValidationError`。可选 embedder 要求 fingerprint/name、dimension
和 `embed_batch(texts)`；可选 `embed_query(text)` 允许非对称 query/document embedding。
向量与 fingerprint 一起持久化，scope 超出 policy 的
`dense_scan_limit` 时默认 SQLite dense 检索拒绝运行。可选 `VectorIndex` 实现通过
scope/fingerprint 过滤召回向量候选；`QdrantVectorIndex(url, timeout=5, api_key=None)` 需
安装 `hermetic-dmsa[vector]`。Qdrant 候选经 SQLite 再次验证 scope。细节与适用边界见
`docs/algorithms/coding-memory.md`。
`max_chars` 按 Python 字符长度计量，`max_tokens` 使用公开的
`dmsa.memory.estimate_tokens` 确定性 UTF-8 字节预算代理，并非模型 tokenizer。SQLite 使用带 scope
约束的索引与 FTS 结构；FTS 虚表按 `trigram` 三元组分词，所以中英混排查询可召回，短于 3 个
字符的词项（如 `db`、两字中文词）改由 scope 内 LIKE 子串通道兜底。存储初始化会执行幂等
schema migration，旧数据库可直接打开，分词器为 `unicode61` 的历史库会被自动重建索引。
`SearchPolicy.experience_weight`（默认 `1.0`，范围 `[0, 10]`）调节可追溯经验卡的轻量
适用性先验；设为 `0` 可在配对回放中禁用该 rerank，而不改变来源记录或召回通道。
`SearchPolicy.evidence_limit`（默认 `12`）、`score_floor_ratio`（默认 `0.45`）、
`min_evidence`（默认 `4`）与 `per_path_limit`（默认 `6`）共同约束单次检索返回的证据条数：
`top_k` 只是上限，返回条数还会被分数下限、单会话与单路径限额截断，避免把整段会话塞进上下文。
写入时同一 session 的多轮消息会合并成一条 `[episode]` 会话级经验卡（症状、根因、最终补丁与
验证结论），只有存在补丁且其后有显式验证时才生成；原始消息切片保持不变。

## Offline RSI

`dmsa.gradient.RetrievalPolicy` wraps an immutable validated `SearchPolicy`.
`run_replay(memory, baseline, candidate, cases, ..., development_only=True)`
performs paired real retrieval against the same unchanged corpus snapshot and
binds report hashes to policies, cases, corpus and fixed budgets. `gate(report)`
rejects empty, mismatched, regressing or budget-invalid evidence and requires a
per-case recall improvement. `PolicyRegistry.register` accepts only reports
executed in the current process through `run_replay`; it atomically persists
candidates and decisions. `rollback(version)` selects a previously accepted
policy. This API is for development evidence; runtime feedback does not train
or activate policies automatically.
# Coding Memory HTTP API (development subset)

`dmsa.mcp_server.http.create_app(memory, api_key)` exposes authenticated `POST
/add`, `POST /search`, and unauthenticated `GET /health`. It does not close the
injected `CodingMemory`; the CLI owns and closes its own database connection.
Authentication accepts `Authorization: Bearer <key>`, `Authorization: Token
<key>`, or `X-Api-Key`. `/add` accepts string content or text-part arrays; image
parts are rejected. Search is isolated by `user_id`; only empty `options` is
currently supported. The `/v1/runs` protocol is not implemented.

Install `hermetic-dmsa[http]` and run `dmsa-memory-http --profile sparse-dev`
for the explicit local sparse-only profile. `--profile competition` supports
`text-embedding-v4` over the existing OpenAI-compatible adapter, or
`qwen3.7-text-embedding` over the DashScope native API adapter. Qwen uses
`DMSA_EMBEDDING_BASE_URL` as the exact endpoint and supports dimensions
256/512/768/1024/1536/2048/2560; it sends document/query text types separately
and splits batches at 20 inputs. No provider credential or runtime call is
implicit.

`dmsa.gradient.ModelAssistedPolicyProposer` selects only candidates emitted by
deterministic `propose_policy`; outputs remain advisory and must pass `run_replay`
and `gate`. It reads `DMSA_DECISION_BASE_URL`, `DMSA_DECISION_API_KEY`, and
`DMSA_DECISION_MODEL`, with text explanations from `DMSA_TEXT_*`. It does not
automatically activate a policy. No official provider compatibility or
competition scale is claimed.
