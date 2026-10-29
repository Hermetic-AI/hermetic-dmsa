## Coding Memory HTTP API (development subset)

集成模式见 [DMSA + RSI](integrated-rsi.md)。`create_app(memory, api_key, rsi=None)`
可注入 `IntegratedRSI(memory, models=False)`。启用时 `/search` 额外返回 `trace_id`；
认证 `/feedback` 保存标签并自动提案、回放和门禁激活；`/rsi/evolve` 可重试，
`/rsi/rollback` 只接受当前 scope 已保存版本。模型开关不影响固定门禁。

需要完整请求/响应、运行命令与 Agent 测试步骤，请先看 [Coding Memory 实操指南](coding-memory-guide.md)。

`from dmsa.mcp_server.http import create_app` 提供无认证 `GET
/health` 与受认证 `POST /add`、`POST /search`、`GET /status`、`GET /metrics`。
认证接受 Bearer、Token 或
`X-Api-Key`。调用方注入的 CodingMemory 归调用方管理；HTTP CLI 管理自己的实例。
消息 content 可为字符串或 `{type: "text", text: ...}` parts；只支持纯文本。
搜索由 `user_id` 隔离，`options` 仅接受空数组。`/v1/runs` 尚未实现。

`GET /status?scope=<id>` 复用 stdio 的 `status_payload`，额外补 `uptime_seconds`、
`embedding.{enabled,fingerprint}` 与 `vector_index`（`sqlite` 或 `qdrant`），用来回答
"配置到底生效了吗"；`GET /metrics` 返回本进程的 `get_registry().export()` 快照加
`uptime_seconds`。两者都不含凭据、请求原文或模型响应。指标随进程重启清零。

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
policy 可用于配对检索，不会修改实例默认策略。`delete_scope(user_id, repo="")` 删除 scope 数据，
不带 `repo` 时连同该用户的全部仓库子域一起清理，带 `repo` 时只清理该分区；
`corpus_state(user_ids)` 返回同一事务读取的 `CorpusState(revision, fingerprint)`，
`corpus_fingerprint(user_ids)` 为其指纹快捷方式，`close()` 关闭连接。
校验错误抛出 `CodingMemoryValidationError`。可选 embedder 要求 fingerprint/name、dimension
和 `embed_batch(texts)`；可选 `embed_query(text)` 允许非对称 query/document embedding。
向量与 fingerprint 一起持久化，scope 超出 policy 的
`dense_scan_limit` 时默认 SQLite dense 检索拒绝运行。可选 `VectorIndex` 实现通过
scope/fingerprint 过滤召回向量候选；`QdrantVectorIndex(url, timeout=5, api_key=None)` 需
安装 `hermetic-dmsa[vector]`。Qdrant 候选经 SQLite 再次验证 scope。细节与适用边界见
`docs/algorithms/coding-memory.md`。
`add(...)`、`search(...)`、`delete_scope(...)` 与 `save_repo_snapshot(...)` 都接受可选的
`repo` 关键字参数；`dmsa.memory.scope_key(user_id, repo)` 返回实际分区键（`repo` 为空时即
`user_id`，否则为 `user#repo`）。分隔符 `#` 不允许出现在 `user_id` 或 `repo` 中，否则
`scope_key` 抛 `CodingMemoryValidationError`，因为复合键必须能反向归属才能按用户完整清理。
`max_chars` 按 Python 字符长度计量，`max_tokens` 使用公开的
`dmsa.memory.estimate_tokens` 确定性 UTF-8 字节预算代理，并非模型 tokenizer。SQLite 使用带 scope
约束的索引与 FTS 结构；FTS 虚表按 `trigram` 三元组分词，所以中英混排查询可召回，短于 3 个
字符的词项（如 `db`、两字中文词）改由 scope 内 LIKE 子串通道兜底。存储初始化会执行幂等
schema migration，旧数据库可直接打开，分词器为 `unicode61` 的历史库会被自动重建索引。
`SearchPolicy.experience_weight`（默认 `1.0`，范围 `[0, 10]`）调节可追溯经验卡的轻量
适用性先验；设为 `0` 可在配对回放中禁用该 rerank，而不改变来源记录或召回通道。
`SearchPolicy.anchor_weight`（默认 `0.6`，范围 `[0, 10]`）控制代码锚点召回通道的 RRF 权重：
锚点来自轨迹原文里的 diff 头、代码块 `path=`、类/函数定义、测试 id 与堆栈帧，写入
`code_anchors` 表并按 scope 索引；设为 `0` 可在配对回放中关闭该通道。命中证据的来源头之后
会追加一行 `[anchors] path::symbol; ...`。
`CodingMemory.save_repo_snapshot(user_id, root, repo="")` 用标准库 `ast` 扫描工作区 Python 文件，保存
仓库相对路径、文件指纹与限定符号；检索时把路径已消失或符号已改名的历史证据标注 `[stale]`
并轻微降权，但原文仍然返回。没有快照时不做失效判断。快照写入与检索相同的分区，因此
仓库级失效标注只作用于该 `repo` 的历史证据。
`SearchPolicy.skeleton_depth`（默认 `6`，范围 `[0, 40]`）控制证据装包时追加的 `[skeleton]`
代码结构行数，设为 `0` 关闭；`SearchPolicy.relation_window`（默认 `0`，范围 `[0, 20]`）开启后
会按 `code_relations`（`defines_symbol` / `touches_file` / `validates_test`）对已选证据做一次
同目标关系扩展，扩展项分数打折后排在原始命中之后。两者都只影响装包，不新增召回通道。
`SearchPolicy.evidence_limit`（默认 `200`）、`score_floor_ratio`（默认 `0.45`）、
`min_evidence`（默认 `4`）、`per_path_limit`（默认 `12`）与 `per_session_limit`（默认 `40`）
共同约束单次检索返回的证据条数：`top_k` 只是上限，返回条数还会被分数下限、单会话与单路径
限额、字符/token 预算截断。默认额度与外部契约固定的 `top_k=100` 同量级，避免把请求量压到
个位数。`SearchPolicy.adjustment_weight`（默认 `0.25`，范围 `[0, 10]`）缩放证据质量调分，
使其不会整体压过通道 RRF 排名。装包的来源头额外带 `[path=…]` 与 `[symbol=…]`；历史整文件
写入按来源消息整体剔除，续接片不再被当成现行代码注入。
写入时同一 session 的多轮消息会合并成一条 `[episode]` 会话级经验卡（症状、根因、最终补丁与
验证结论），只有存在补丁且其后有显式验证时才生成；原始消息切片保持不变。
持久向量按 float32 字节存放（1024 维约 4KB），并带一列正文摘要用于内容寻址复用：同一段正文
在同一模型下只计算一次向量，重复正文直接复用，检索结果不变。

## CodingMemory 扩展能力

以下能力与 [Coding Memory 扩展能力](algorithms/coding-memory-extensions.md) 一一对应，
全部是工程增强，不构成论文结论，也不声称提升编码任务成功率。

### 写入门控与遗忘

`CodingMemory.add_guarded(request_id, user_id, session_id, messages, policy=None, repo="",
author="")` 返回 `AdmissionResult(revision, decision)`；`AdmissionDecision` 带 `admitted` /
`reason` / `novelty` / `duplicate_of` / `rival_author`。判定原因只有 `novel`（正常写入）、
`idempotent`（幂等重试，返回原 revision）、`empty_content`（拒绝）、
`redundant`（同作者近似重复，拒绝）与 `conflict_candidate`（跨作者近似重复，保留并登记
写冲突）。`CodingMemory.add(...)` 语义不变，仍返回 scope revision。门控阈值由
`SearchPolicy.admission_similarity`（默认 `0.95`，范围 `[0, 1]`）给出。

`dmsa.memory.MemoryGovernance` 提供治理动作，绑定在 `CodingMemory.governance` 上：
`record_exposure(user_id, chunk_ids, repo="")` 记账曝光、`trust_report(user_id, repo="")`
返回全部条目的信任度与观测计数、`record_verdict(user_id, chunk_ids, repo="", *, confirmed,
contradicted, verified)` 按反馈或任务结果更新计数、`forget(user_id, repo="", *, floor=0.1,
min_age_days=30)` 淘汰长期未使用且低信任的条目、`restore(user_id, chunk_ids, repo="")`
取消淘汰标记。淘汰只打标记不删原文，原文仍可按 ID 追溯。

`dmsa.memory.MemoryStat` 是单条目观测统计，信任度由计数在读取时推导（基线 `0.5`，
确认 `+0.15`、反驳 `-0.1`、验证 `+0.05`，夹在 `[0.02, 1.0]`，再按 30 天半衰期衰减）。
`SearchPolicy.trust_weight`（默认 `0.02`，范围 `[0, 1]`）控制信任度以乘子形式进入排序的
幅度。

`dmsa.gradient.attribution` 提供条目级因果归因：`attribute_feedback` /
`attribute_task` 只消费 Agent 明示的信号，失败类型只有 `coarse_chunk`、
`identifier_confusion`、`stale_evidence` 才反驳召回条目；`attribute_trace_feedback` 与
`attribute_trace_task` 按轨迹的仓库子域定位分区后写入统计。两者已接进
`RetrievalFeedbackStore.feedback_with_attribution(...)` 与
`IntegratedRSI.record_task_result(...)`。

### 端到端有效性度量

`dmsa.gradient.effectiveness.measure_effectiveness(path, scope)` 返回
`EffectivenessReport`：检索侧给 `label_recall` 与 `hit_precision`，任务侧按
`delivered` / `undelivered` / `unlabeled` 分组给任务数与成功率。任务侧是观察性对比
（`observation` 字段显式标注口径），不是受控 A/B；样本不足时各项如实留空（`None`）。

### 程序性记忆

`CodingMemory.add_procedure(request_id, user_id, session_id, messages, repo="")` 从会话
沉淀流程，返回 `Procedure` 或 `None`（没有显式有序步骤时不落盘）。`Procedure` 带
`title` / `steps` / `preconditions` / `verification` / `trigger_terms` / `paths` /
`outcome`；有验证行时 `outcome` 记 `reported_success`，否则 `unknown`。
`CodingMemory.search_procedures(query, user_id, top_k=3, repo="")` 返回
`ProcedureMatch`，其 `score` 是命中词项数。

### 团队作用域与写冲突仲裁

`dmsa.memory.team_scope(team_id, repo="")` 返回带 `team:` 前缀的团队共享作用域键，
成员共享同一证据空间，仓库子域依旧隔离。`CodingMemory.team_scope(team_id, repo="")`
是等价静态方法。

`CodingMemory.conflicts(user_id, repo="", *, status="", limit=20)` 列出 `WriteConflict`
（`id` / `scope` / `chunk_id` / `rival_chunk_id` / `author` / `rival_author` /
`similarity` / `status` / `resolution` / `created_at` / `resolved_at`）。
`CodingMemory.resolve_conflict(user_id, conflict_id, resolution, repo="")` 返回
`ConflictResolution`（`conflict` / `applied` / `confirmed` / `contradicted`）。仲裁结论
只有 `keep_new` / `keep_rival` / `keep_both`；前两者会把结论写回双方的信任计数。

### 多跳检索与召回解释

`CodingMemory.search_explained(query, user_id, top_k, policy=None, repo="", hops=1)`
返回 `RecallExplanation`：`rounds` 是每轮的查询、补充词与候选数（`RecallRound`），
`items` 逐条给 `channels` / `matched_terms` / `paths` / `symbols` / `reason`
（`RecallItem`）。跳数上限 3；扩展只影响候选范围，不改变打分口径。

### 记忆合并与跨仓库迁移

`CodingMemory.consolidate(user_id, repo="", *, min_group=2, limit=500)` 返回
`ConsolidationReport`（`scope` / `groups` / `written` / `skipped`），派生条目保留全部
来源 ID，原始证据不被删除，同一规则重复合并幂等。
`CodingMemory.transfer(user_id, source_repo, target_repo, *, limit=500)` 返回
`TransferReport`（`source_scope` / `target_scope` / `transferred` / `skipped`）；含具体
路径与符号的规则被拒绝并在 `skipped` 里列出泄漏项。
`CodingMemory.consolidation_records(user_id, repo="")` 与
`CodingMemory.transfer_records(user_id, repo="")` 列出历史记录（`ConsolidationRecord` /
`TransferRecord`）。`dmsa.memory.Generalization` 与 `dmsa.memory.RuleGroup` 描述泛化后的
规则与规则组，`transferable` 表示是否可跨仓库迁移。

### 稠密混合检索与重排

`CodingMemory.search_hybrid(query, user_id, top_k, policy=None, repo="", hybrid=None)`
返回 `HybridResult`（`query` / `hits` / `items` / `channels` / `dense_available`）；
`HybridItem` 给出每条证据的 `channels` / `contributions` / `agreement` /
`dense_similarity` / `rerank_delta`。`HybridPolicy`（`dense_weight` /
`agreement_bonus` / `redundancy_penalty` / `rerank_delta` / `rerank_depth` /
`dense_min_similarity`）单独承载混合检索参数，不写进 `SearchPolicy`，因此 RSI 的基线与
回放样本不受影响。没有嵌入器或没有稠密候选时 `dense_available` 为 `False`。

### 时序 as-of 查询

`dmsa.memory.AsOf(revision=None, timestamp=None)` 是时序坐标，至少要给一个坐标，两者
都给时取交集；取值为空、负数或非整数时抛 `CodingMemoryValidationError`。
`revision` 走 `requests.revision`（作用域内每次成功写入递增，历史完整），`timestamp`
走 `memory_stats.created_at`（条目写入时刻的 Unix 秒；写入时同步建立统计行，因此覆盖
全部新写入条目，旧库数据应改用 `revision`）。

`CodingMemory.search_as_of(query, user_id, top_k, as_of, policy=None, repo="")` 返回
`TemporalResult`（`query` / `hits` / `as_of` / `scope_revision`）。过滤在 SQL 层完成，
所有召回通道统一生效；`scope_revision` 给出作用域当前的写入序号。普通
`CodingMemory.search(...)` 的行为不变。

## stdio MCP 工具清单

`dmsa_coding_memory_*` 系列工具只在 stdio MCP（`dmsa-coding-memory-mcp`）暴露；HTTP
路径目前只有 `/add`、`/search`、`/feedback`、`/task_result`、`/rsi/evolve`、
`/rsi/rollback` 与 `/health`，不包含下表。

| 工具 | 参数 | 用途 |
|---|---|---|
| `dmsa_coding_memory_search` | `query`、`top_k`、`task_id?`、`repo?` | 检索证据，返回 `trace_id` |
| `dmsa_coding_memory_record` | `request_id`、`session_id`、`content`、`author?`、`repo?` | 写入经验，回报门控判定 |
| `dmsa_coding_memory_status` | 无 | 确认连接、scope 与当前活动策略 |
| `dmsa_coding_memory_feedback` | `trace_id`、`outcome`、`kind?`、`relevant_ids?` | 保存检索反馈，触发受门禁保护的策略进化 |
| `dmsa_coding_memory_task_result` | `task_id`、`trace_id`、`outcome`、`tests_passed?`、`files_changed?`、`tokens?`、`latency_ms?`、`detail?` | 上报真实任务结果 |
| `dmsa_coding_memory_governance` | `repo?`、`forget?`、`floor?`、`min_age_days?` | 查看信任度，可选执行遗忘淘汰 |
| `dmsa_coding_memory_procedure` | `action?`、`request_id?`、`session_id?`、`content?`、`query?`、`top_k?`、`repo?` | 沉淀或匹配程序性记忆 |
| `dmsa_coding_memory_effectiveness` | 无 | 度量检索侧标签召回率与任务侧分组成功率 |
| `dmsa_coding_memory_conflicts` | `action?`、`conflict_id?`、`resolution?`、`status?`、`limit?`、`repo?` | 列出或仲裁跨作者写冲突 |
| `dmsa_coding_memory_recall` | `query`、`top_k?`、`hops?`、`repo?` | 带召回解释的检索，可做多跳扩展 |
| `dmsa_coding_memory_consolidate` | `repo?`、`min_group?`、`limit?` | 合并重复经验，返回合并报告 |
| `dmsa_coding_memory_transfer` | `source_repo?`、`target_repo`、`limit?` | 跨仓库迁移通用规则，返回迁移报告 |
| `dmsa_coding_memory_hybrid_search` | `query`、`top_k?`、`repo?` | 稠密混合检索，返回融合明细与重排增量 |
| `dmsa_coding_memory_search_as_of` | `query`、`top_k?`、`as_of_revision?`、`as_of_timestamp?`、`repo?` | 时序回看检索，返回当时可见的内容与当前写入序号 |

## Offline RSI

`dmsa.gradient.task_results.TaskResult` 与 `TaskResultStore` 保存真实任务结果
（任务标识、关联 trace、成败、通过测试数、改动文件、token 与耗时），按 scope 隔离且同
`task_id` 幂等；`IntegratedRSI.record_task_result(...)` 与 `IntegratedRSI.task_results(scope)`
提供读写入口。结果只作为事实记录，系统不从失败反推根因，也不会据此直接激活策略。
检索轨迹新增语料指纹、任务标识与仓库分区列，旧库打开时自动补列。
`dmsa.gradient.task_gate.task_cases(scope, traces, labels, outcomes)` 把轨迹、已确认证据与
任务终态拼成 `TaskCase`（只有同时具备终态与确认证据的轨迹才成为样本），
`run_task_gate(memory, baseline, candidate, cases)` 要求候选策略保留每个已成功任务当时被确认
的证据；`TaskGateReport.passed` 为假时 `IntegratedRSI.evolve` 拒绝激活并写
`task_gate_rejected` 事件。没有任何任务样本时门禁是空过（`checked=0`），事件里如实记录。
`dmsa.gradient.meta_evolution.compare_proposers(rsi, scope, budget_replays=6,
proposer_factory=None, observer=None)` 在等回放预算下比较确定性提案器与模型辅助提案器，
返回 `MetaEvolutionReport`（每臂 `status` / `candidates` / `replays` / `accepted` /
最优均值指标 / `error_type`），只写一条 `meta_evolution` 审计事件，不激活任何策略；
`budget_replays` 必须能让每个提案器至少跑一次回放，否则抛 `OfflineRSIError`。
仅当某臂存在通过门禁的候选时才给出 `winner`，平局判给确定性提案器。

`dmsa.gradient.RetrievalPolicy` wraps an immutable validated `SearchPolicy`.
`run_replay(memory, baseline, candidate, cases, ..., development_only=True)`
performs paired real retrieval against the same unchanged corpus snapshot and
binds report hashes to policies, cases, corpus and fixed budgets. `gate(report)`
rejects empty, mismatched, regressing or budget-invalid evidence. The current gate
(`offline-rsi-gate-v2`) additionally requires no per-case recall regression, no per-case NDCG
regression, no token growth on cases without a recall gain, and at least one strict improvement
in recall or ordering quality. `PolicyRegistry.register` accepts only reports
executed in the current process through `run_replay`; it atomically persists
candidates and decisions. `rollback(version)` selects a previously accepted
policy. This API is for development evidence; runtime feedback does not train
or activate policies automatically.
# Coding Memory HTTP API (development subset)

`dmsa.mcp_server.http.create_app(memory, api_key)` exposes authenticated `POST
/add`, `POST /search`, `POST /feedback`, `POST /task_result`, `POST /rsi/evolve`,
`POST /rsi/rollback`, and unauthenticated `GET /health`. `POST /search` accepts an optional
`task_id` that is bound to the retrieval trace; `POST /task_result` stores the verified task
outcome separately from retrieval feedback. `/add` and `/search` also accept an optional
`repo` string that selects the repository partition (`user#repo`); `/add` echoes it back so
callers can confirm which partition was written. It does not close the
injected `CodingMemory`; the CLI owns and closes its own database connection.
Authentication accepts `Authorization: Bearer <key>`, `Authorization: Token
<key>`, or `X-Api-Key`. `/add` accepts string content or text-part arrays; image
parts are rejected. Search is isolated by `user_id`; only empty `options` is
currently supported. The `/v1/runs` protocol is not implemented.

Install `hermetic-dmsa[http]` and run `dmsa-memory-http --profile sparse-dev`
for the explicit local sparse-only profile. `--profile competition` supports
`text-embedding-v4` over the existing OpenAI-compatible adapter, or
`qwen3.7-text-embedding-flash` over the DashScope native API adapter. Qwen uses
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

## 日志 API

`dmsa.observability` 导出日志门面，全部基于 stdlib，无第三方依赖。

| 入口 | 作用 |
| --- | --- |
| `get_logger(name)` | 取 `JsonLogger`，方法 `trace/debug/info/warning/error/exception/log`，关键字参数即结构化字段 |
| `setup_logging(level=None, stream=None, *, fmt=None, log_file=None)` | 服务/CLI 入口装配，未传参时读 `DMSA_LOG_*` 环境变量，返回生效级别 |
| `configure_root(...)` | `setup_logging` 的底层实现，额外支持 `color` 与 `force` |
| `add_logging_args(parser)` / `apply_cli_logging(args)` | 给 argparse 挂 `--log-level/--log-format/--log-file` 并按参数装配 |
| `parse_level` / `parse_format` / `should_colorize` / `quiet_third_party` | 单项解析与三方降噪，非法级别或格式抛 `InvalidConfigValueError` |
| `TextFormatter` / `JsonFormatter` | 可单独使用；两者都输出 `logger:func:line` 与 `trace_id` |
| `trace_context(trace_id)` / `bind_trace_id` / `current_trace_id` | 用 contextvar 给一段调用链绑定 trace ID |

约定：`JsonLogger` 通过 `stacklevel` 上报真实调用点，因此 `func`/`line` 指向业务代码而
不是包装器内部；重复装配日志是幂等的，只替换本模块自己安装的 handler，不影响宿主或测试
框架已挂载的 handler。默认输出流是 stderr，因为 stdio MCP 与若干 CLI 把 stdout 留给协议
和结果本身。

## 指标 API

`dmsa.observability` 同时导出零依赖的 metrics 门面。指标按进程累积，重启清零；要跨进程
看历史仍然依赖 SQLite 审计表，指标只负责"现在这台机器正在发生什么"。

| 入口 | 作用 |
| --- | --- |
| `get_registry()` | 全局注册表；`counter(name)`、`histogram(name, buckets=)`、`count_histogram(name)`、`export()`、`reset()` |
| `timed(latency_name, *, registry=None, buckets=)` | 上下文管理器，写入 `<stem>.latency` / `.calls` / `.failures`，异常原样抛出 |
| `observe_count(name, value, *, registry=None)` | 记录"个数"形态观测，自动使用 `COUNT_BUCKETS` |
| `Counter` / `Histogram` | 单调计数与累积分桶；`Histogram.to_dict()` 附 `min`/`max`/`mean`/`p50`/`p95`/`p99` |
| `DEFAULT_BUCKETS` / `COUNT_BUCKETS` | 延迟（秒，1ms-10s）与规模（1-500）两套桶，按观测量形态选择 |
| `start_span(name, **attrs)` | 退出时把耗时写入 `dmsa.span.<name>.latency`，异常写入 `.failures` |

约定：未观测到的统计量导出为 `null`，不用 `0` 冒充测量结果；落在 `+Inf` 桶的分位数返回
`null`，表示超出量程而不是"很快"。`Counter.inc()` 拒绝负数，抛 `MetricValueError`
（`DMSAError` 与 `ValueError` 双继承，既有 `except ValueError` 兜底不受影响，错误码
`DMSA-0003`）。指标名基数有界：路由名走固定映射，span 名是代码里的字面量。

现有埋点：`dmsa.http.latency`、`dmsa.http.<route>.latency`、`dmsa.http.requests.<4xx|5xx|…>`、
`dmsa.http.health_checks`、`dmsa.search.latency`、`dmsa.search.returned`、
`dmsa.embedding.query.latency`、`dmsa.embedding.batch.latency`、`dmsa.embedding.batch.items`、
`dmsa.vector_index.latency`、`dmsa.vector_index.returned`。健康检查只计数、不进延迟直方图，
否则探针流量会淹没真实请求的延迟分布；`dmsa.search.returned` 直接对应平台写死的 `top_k`
交付契约，比排序质量更该先被看见。
