## Coding Memory HTTP API (development subset)

`from dmsa.mcp_server.http import create_app` 提供无认证 `GET
/health` 与受认证 `POST /add`、`POST /search`。认证接受 Bearer、Token 或
`X-Api-Key`。调用方注入的 CodingMemory 归调用方管理；HTTP CLI 管理自己的实例。
消息 content 可为字符串或 `{type: "text", text: ...}` parts；只支持纯文本。
搜索由 `user_id` 隔离，`options` 仅接受空数组。`/v1/runs` 尚未实现。

安装 `.[http]` 后 `dmsa-memory-http --profile sparse-dev` 运行离线稀疏开发
模式。`competition` profile 要求显式 endpoint、embedding key 与
`text-embedding-v4` 模型。PMD 仍是 workspace sibling 依赖，当前包不能独立部署。

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

PMD 目前是 workspace sibling 路径依赖，本页不承诺独立公开安装。

## CodingMemory

`from dmsa.memory import CodingMemory, SourceMessage, SearchPolicy` 提供独立于 DAG 的
scope coding evidence API。`CodingMemory(path, embedder=None, policy=None)` 接受 SQLite
路径；`add(request_id, user_id, session_id, messages)` 幂等保存有序 `SourceMessage`
并返回 scope revision；冲突抛出 `CodingMemoryConflictError`。`search(query, user_id,
top_k, policy=None)` 返回含稳定切片 ID、来源引用文本和 RRF 分数的 `SearchHit`。临时
policy 可用于配对检索，不会修改实例默认策略。`delete_scope(user_id)` 删除 scope 数据，
`corpus_state(user_ids)` 返回同一事务读取的 `CorpusState(revision, fingerprint)`，
`corpus_fingerprint(user_ids)` 为其指纹快捷方式，`close()` 关闭连接。
校验错误抛出 `CodingMemoryValidationError`。可选 embedder 要求 fingerprint/name、dimension
和 `embed_batch(texts)`；向量与 fingerprint 一起持久化，scope 超出 policy 的
`dense_scan_limit` 时 dense 检索拒绝运行。细节与适用边界见 `docs/algorithms/coding-memory.md`。
`max_chars` 按 Python 字符长度计量，`max_tokens` 使用公开的
`dmsa.memory.estimate_tokens` 确定性 UTF-8 字节预算代理，并非模型 tokenizer。SQLite 使用带 scope
约束的索引与 FTS 结构；存储初始化会执行幂等 schema migration，旧数据库可直接打开。

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
for the explicit local sparse-only profile. `--profile competition` requires a
configured OpenAI-compatible endpoint and API key, model `text-embedding-v4`,
and a positive dimension configured with `--dimension` (default 1536). No official provider compatibility or competition scale is
claimed.
