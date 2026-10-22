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

`snapshot` / `rollback` / `list_versions` 继续委托 PMD。
`export_state` / `import_state` 保留 `dmsa.memory.v1` 格式。
导入产生独立内存副本，保留 payload 的配置元数据，但不会重新打开并修改其原始数据库。

当前 PMD 的 SQLite rollback 只恢复 live DAG，不删除快照后新增的持久化行；
重新打开数据库可能重新加载这些行。调用方不应将其视为 durable rollback。
PMD 目前是 workspace sibling 路径依赖，本页不承诺独立公开安装。
