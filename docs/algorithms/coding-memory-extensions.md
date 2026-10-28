# Coding Memory 扩展能力

本文覆盖 CodingMemory 在基础检索之上新增的一组工程能力：写入门控与遗忘淘汰、条目
信任模型与因果归因、端到端有效性度量、程序性记忆、团队作用域与写冲突仲裁、多跳
迭代检索与召回解释、记忆合并与跨仓库迁移、稠密混合检索与重排、时序 as-of 查询。

基础检索（切片、FTS5 三元组、通道融合、证据装包、仓库分区）见
[Coding Memory 证据检索](coding-memory.md)。

这些能力全部是工程增强，不是论文已验证的创新点，也不构成编码成功率的实证结论。
它们的可测边界写在每节末尾。

## 写入门控

`coding.admission` 在写入前判断一次请求是否值得进入记忆。比较用词项集合的 Jaccard
相似度：英文按词切分，中文按二元字组切分，不依赖分词器，也不把整段文本当成一个词项。

判定结果只有五种，每种都带可解释的原因：

| 原因 | 含义 | 是否落盘 |
|---|---|---|
| `novel` | 与既有记忆不重复 | 是 |
| `idempotent` | 同一 request 已写入（幂等重试） | 否，直接返回原 revision |
| `empty_content` | 没有可用文本 | 否 |
| `redundant` | 与既有条目近乎重复且同一位作者 | 否 |
| `conflict_candidate` | 近似重复但来自另一位作者 | 是，并登记一条待仲裁的写冲突 |

`CodingMemory.add_guarded(...)` 返回 `AdmissionResult(revision, decision)`；
`AdmissionDecision` 带 `admitted` / `reason` / `novelty` / `duplicate_of` / `rival_author`。
原来的 `CodingMemory.add(...)` 语义不变，返回 scope revision。

阈值由 `SearchPolicy.admission_similarity` 给出。门控只在有明确理由时拒绝写入：
同义重复被抑制，但跨作者的相反结论不会被当成噪音丢掉。

## 遗忘衰减与淘汰

`coding.trust` 定义条目信任度，只由可核验的观测驱动：

- 曝光（`retrieved`）：条目被检索返回过；
- 确认（`confirmed`）：反馈显式把该来源标为相关，是最强的正向证据；
- 反驳（`contradicted`）：条目出现在失败轨迹的召回集合里，且失败类型指向召回内容本身有错；
- 验证（`verified`）：任务成功时该条目仍在证据集中，是弱正向证据。

信任度不落库，由观测计数在读取时推导：基线为 `0.5`，每条确认 `+0.15`、每条反驳
`-0.1`、每条验证 `+0.05`，夹在 `[0.02, 1.0]`；再按距上次使用的时间做 30 天半衰期
衰减。没有被观测到的条目保持中性，只有明确的负面证据才会低于中性值。

曝光来自检索路径本身：stdio MCP 的 `search` 在返回结果后调用
`MemoryGovernance.record_exposure(...)` 记账，把「被捞出来过」变成可核验的观测。
检索排序时信任度以乘子形式进入分数（`SearchPolicy.trust_weight`，默认 `0.02`，
范围 `[0, 1]`），中性条目系数为 `1.0`，因此信任调整不会压过词面与稠密信号。

`MemoryGovernance.forget(user_id, repo, floor=0.1, min_age_days=30)` 淘汰长期未被使用
且信任度低于阈值的条目。淘汰只打标记不删原文，原文仍可按 ID 追溯；
`MemoryGovernance.restore(...)` 可以取消标记让条目重新参与检索。被淘汰的条目不再进入
检索结果。

## 条目级因果归因

`gradient.attribution` 把显式反馈与任务结果映射到条目级的信任更新。它只消费 Agent
明示的信号，不从失败反推根因：

- 反馈中被点名的条目记为「已确认」；
- 失败类型明确指向召回内容本身有错时（`coarse_chunk`、`identifier_confusion`、
  `stale_evidence`），召回但未被点名的条目记为「已反驳」；
- 「证据不足」类失败（`exact_miss`、`missing_context`、`missing_relation`、
  `missing_structure`）不构成对召回条目的反驳——缺什么和召回错了是两回事；
- 任务成功把该轨迹召回的条目记为「已验证」，任务失败不产生任何归因。

`attribute_trace_feedback(db, scope, trace_id, outcome, kind, relevant_ids)` 与
`attribute_trace_task(db, scope, trace_id, outcome)` 按轨迹的仓库子域定位分区后写入
条目统计，返回 `{"status": "applied" | "no_recall", ...}`。两者已经接进主链路：
`RetrievalFeedbackStore.feedback_with_attribution(...)` 在保存反馈时调用前者，
`IntegratedRSI.record_task_result(...)` 在保存任务结果时调用后者，因此归因不依赖调用方
额外记账。轨迹不存在或没有召回内容时返回 `no_recall`，不做任何条目级更新。

## 端到端有效性度量

`gradient.effectiveness.measure_effectiveness(path, scope)` 度量记忆是否真的起作用，
分两侧：

- 检索侧：`label_recall` 是反馈里被确认有用的证据有多少真的出现在当次召回里；
  `hit_precision` 是召回集合里被确认有用的比例。
- 任务侧：真实任务结果按「记忆把被确认有用的证据送到了任务上」（`delivered`）与
  「记忆没送到」（`undelivered`）分组，给出两组的任务数与成功率。

任务侧是观察性对比（观测谁拿到了有用证据），不是受控 A/B，报告里用 `observation`
字段显式标注口径，不宣称因果。样本不足或链路缺失时如实留空（`None`），不用别的口径
补齐；没有任何样本时 `labeled_traces` 为 0，各比率为 `null`。

## 程序性记忆

事实型记忆回答「发生过什么」，程序性记忆回答「下次该按什么顺序做」。
`coding.procedures` 用确定性规则从会话里抽取流程，只认显式的有序步骤、前置条件与
验证说明，不推断未写出的步骤：步骤行必须匹配数字编号或常见列表符号，且至少两条；
标题优先取 Markdown 标题，其次取首条用户消息首行。少于两条步骤或没有标题时
`build_procedure` 返回 `None`，`CodingMemory.add_procedure(...)` 也返回 `None`，
不落盘。

`Procedure` 带 `title` / `steps` / `preconditions` / `verification` / `trigger_terms` /
`paths` / `outcome`。有验证行时 `outcome` 记 `reported_success`，否则记 `unknown`——
自述不会被升级成已验证事实。

检索用 `procedure_terms` 展开词项：ASCII 词元加上中文 2 gram 与 3 gram，因此「缓存」
这类两字查询能精确命中流程索引，不必退化成子串匹配。`CodingMemory.search_procedures(query,
user_id, top_k=3, repo="")` 返回 `ProcedureMatch`，其 `score` 是命中词项数，
`matched_terms` 列出真正命中的词项。

## 团队作用域与写冲突仲裁

个人作用域里近似重复的写入是噪音，门控直接拒绝。团队作用域里同一个问题会被不同成员
各写一遍，双方可能给出相反结论——这样的近似重复不是噪音而是待仲裁的分歧：写入被保留
并登记为冲突候选，由仲裁决定哪一方是权威版本。

`coding.teams.team_scope(team_id, repo="")` 返回带 `team:` 前缀的共享作用域键，
成员共享同一证据空间，仓库子域依旧隔离。前缀保证按个人清理数据时不会误删团队证据。

仲裁结论同时是信任证据：被保留的一方记「已确认」，被否定的一方记「已反驳」，因此
冲突解决会立刻反馈到检索排序，而不是停在一条待办列表里。

| 结论 | 处置 |
|---|---|
| `keep_new` | 保留新写入（记确认），既有写入记反驳 |
| `keep_rival` | 保留既有写入（记确认），新写入记反驳 |
| `keep_both` | 两者并存，不做信任惩罚 |

`CodingMemory.conflicts(user_id, repo, status="", limit=20)` 列出 `WriteConflict`，
`CodingMemory.resolve_conflict(user_id, conflict_id, resolution, repo)` 返回
`ConflictResolution`（含 `applied` / `confirmed` / `contradicted`）。重复仲裁同一条冲突
不会重复记账，直接返回已关闭的结论。

## 多跳迭代检索与召回解释

单跳检索只按用户原话召回，原话与真实代码标识符对不上时会漏掉证据。
`coding.multihop` 在首轮命中之后，用命中证据的路径与符号做有界扩展，再召回一轮，
让同一处代码的其它证据有机会进入结果。扩展是有界的：每轮取前 `SEED_HITS`（3）条命中的
路径与符号，单轮最多补 `MAX_EXPANDED_TERMS`（8）个扩展词，原查询里已有的词会被跳过。
扩展词只来自已有证据的路径与符号，不引入外部模型，因此结果完全可复现。

扩展只影响「召回到哪些候选」，不改变打分口径：最终排序仍按原查询的特征与原策略计算，
因此多跳结果与单跳结果可比。跳数上限为 3（`MAX_HOPS`），超过三跳后新增候选迅速收敛，
成本却线性上升。

`CodingMemory.search_explained(query, user_id, top_k, policy=None, repo="", hops=1)`
返回 `RecallExplanation`：

- `rounds` 记录每一轮用了什么查询、补充了哪些词、召回到多少候选；
- `items` 逐条给出 `channels`（命中通道）、`matched_terms`（真正出现在正文、路径或符号里的
  查询词项）、`paths`、`symbols` 与一句人读的 `reason`。

## 记忆合并与跨仓库迁移

`coding.generalize` 把带具体路径与符号的经验泛化成可跨仓库复用的规则。泛化只做确定性
的文本替换：正文里出现的具体路径换成 `<path>`，具体符号换成 `<symbol>`。替换之后仍然能
在正文里找到的具体标识算作泄漏，这类规则不允许迁移——在别的仓库里那些路径与符号指向
完全不同的代码。

分组口径是「泛化后正文完全相同」：同一条经验在多次会话里换了具体文件，泛化后落到同一个
规则键上，从而被合并成一条；正文不同的经验不会因为共享某个关键词而被并到一起。
规则键是泛化正文的 SHA-256，相同规则得到相同键。

`CodingMemory.consolidate(user_id, repo, min_group=2, limit=500)` 把重复出现的经验合并
成一条通用规则，派生条目保留全部来源 ID，原始证据不被删除；同一规则重复合并是幂等的，
`ConsolidationReport.skipped` 指出已经合并过的规则。

`CodingMemory.transfer(user_id, source_repo, target_repo, limit=500)` 把不含具体路径与
符号的通用规则复制到目标仓库的子域。被拒绝的规则与泄漏项在 `TransferReport.skipped` 里
列出（`repo_specific:<泄漏项>`）。派生条目的幂等键带上来源作用域，因此同一条规则从两个
仓库迁到同一个仓库时会各自留一条记录，不会被后一次迁移当成重复而丢掉来源。

## 稠密混合检索与重排

`coding.hybrid` 在普通检索之上做两件事：

1. **融合**：词面通道沿用 RRF 名次折算，稠密通道改用批内余弦相似度的极值归一。语义上
   更接近的证据拿到的稠密分明显更多，而不是只按名次先后拿到几乎相同的分数。稠密分折算
   到同一个 RRF 单位后与词面分相加，因此混合分与既有检索分同量纲，可以直接叠加经验分、
   证据质量调整与信任乘子。
2. **重排**：跨通道一致命中的条目小幅上调，与更高排名条目近乎重复的小幅下调，调整幅度被
   `rerank_delta` 夹住。只有前 `rerank_depth` 名候选参与重排，深度之外的分数保持不变。

`HybridPolicy` 单独承载混合检索特有的参数（`dense_weight` / `agreement_bonus` /
`redundancy_penalty` / `rerank_delta` / `rerank_depth` / `dense_min_similarity`），
不写进 `SearchPolicy`，因此 RSI 的基线与回放样本不受影响。

`CodingMemory.search_hybrid(query, user_id, top_k, policy=None, repo="", hybrid=None)`
返回 `HybridResult`，其中 `items` 给出每条证据的通道贡献（`contributions`）、一致通道数
（`agreement`）、稠密相似度与重排增量（`rerank_delta`）；`dense_available` 如实报告稠密
通道是否真的参与——没有嵌入器或没有候选时为 `False`，不做静默回退。

## 时序 as-of 查询

`coding.temporal` 提供两种互补的坐标：

| 坐标 | 来源 | 覆盖范围 |
|---|---|---|
| `revision` | `requests.revision`，作用域内每次成功写入递增 | 历史完整 |
| `timestamp` | `memory_stats.created_at`，条目写入时刻的 Unix 秒 | 只有带统计行的条目 |

写入时每条切片都会同步建立一条中性统计行（`first_revision` 与 `created_at` 一并落库），
因此时间戳口径通常覆盖全部新写入条目；统计表建立之前写入的旧库数据不在该口径的可见
范围内，此时应改用 `revision`。

`AsOf(revision=..., timestamp=...)` 至少要给一个坐标，两个都给时取交集；取值为空、负数
或非整数时抛 `CodingMemoryValidationError`。

**过滤必须在 SQL 层完成**：候选限额是按通道截断的，如果先召回再过滤，较新的证据会先把
名额占满，较旧的目标证据反而被挤出候选集。因此 `as_of_filter` 直接生成 `EXISTS` 子句
拼进各通道的 WHERE，所有通道（精确、稀疏、子串、锚点、稠密）统一生效。

`CodingMemory.search_as_of(query, user_id, top_k, as_of, policy=None, repo="")` 返回
`TemporalResult`，其中 `scope_revision` 是作用域当前的写入序号，便于确认「回看到哪一步」。
普通 `search(...)` 的行为不变。

两个边界需要注意：

- 远端向量索引没有时间维度，因此 as-of 查询会把远端候选请求放宽 4 倍
  （`AS_OF_DENSE_WIDENING`），再用 `rows_by_ids(..., as_of)` 在 SQLite 侧按时间过滤，
  避免未来条目从远端漏进来。SQLite 仍是事实来源。
- 同一条消息的邻居切片与父切片共享同一个 request 与 `created_at`，因此不过滤；
  关系扩展邻居可能来自更晚的写入，需要过滤。

## 可测边界

以上能力都由确定性规则实现，不调用模型、不执行文本中的命令，也不把推断或自述升级为
事实。它们的收益只能由代理噪声基准与真实 Coding Agent 配对任务测量：

- 写入门控的抑制比例、淘汰命中率需要在留出集上测；
- 信任度与因果归因的正确性可以由单测覆盖，但「信任提升是否带来更好的排序」需要配对回放；
- 有效性度量的任务侧是观察性对比，不能替代受控 A/B；
- 合并与迁移的泄漏判定是确定性文本规则，不能替代人工审阅；
- 混合检索与多跳的重排收益需要固定语料上的配对检索测量；
- as-of 查询的正确性由「查询层过滤而非召回后过滤」的回归测试保证。

任何一项都不构成论文实验结论，也不声称提升编码任务成功率。
