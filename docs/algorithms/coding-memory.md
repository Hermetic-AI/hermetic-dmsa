# Coding Memory 证据检索

`CodingMemory` 在用户 scope 内保存原始有序消息，并按消息切成最多 1800 字符的重叠
片段。每个片段保留原消息字符偏移、角色、请求、会话、路径、符号和保守 outcome。
出现错误/失败文本时标记 `observed_failure`；显式通过文本标记为
`reported_success`；其他内容标记 `unknown`。它不执行输入代码，也不把自述提升为验证成功。

SQLite WAL 事务同时写入幂等 request、原始消息、FTS5 切片和 scope revision。
幂等哈希包含 session 与完整、有序消息字段。相同重试返回原 revision，不同内容冲突。
普通 `chunks` 表用主键、scope 和邻居复合索引保存元数据，FTS5 通过 rowid 连接；
scope 编码为独立全文词项，与查询联合匹配后才应用候选上限，避免扫描其他用户的全部切片。
早期直接存入 FTS 虚表的数据库会原子迁移并保留稳定 ID。精确路径/符号与 FTS 通道通过
稳定的 reciprocal-rank fusion (RRF) 合并；精确词项通道按命中词项数排序，子串通道按命中
词项数排序后再截断候选。选取阶段按会话限额和字符预算控制重复。
证据文本包含 request/session/message 及字符 offset 引用。

FTS5 虚表使用 `tokenize='trigram'`（`src/dmsa/memory/coding/schema.py`）。`unicode61` 把
一整段无空格文本当成单一 token，中文查询因此零召回；trigram 按字符三元组建索引，
CJK 与 ASCII 混排都能命中，并支持子串匹配。三元组带来一个下限：任何短于 3 个字符的词项
没有对应三元组，例如 `db`、`脱敏`、`游标`。`tokenize.query_terms` 在查询侧把 CJK 连续段
展开成滑窗三元组、ASCII 段保留整词，去重后上限 64 个词项，再以 `OR` 组合进带 scope 约束的
MATCH 表达式按 BM25 排序；短于窗口的词项交给 `storage_queries.substring_candidates` 的
LIKE 兜底通道，同 scope 内对 `text/paths/symbols` 做转义后的字面子串匹配并以 `candidate_limit`
限额，RRF 权重沿用 `sparse_weight`。LIKE 是 scope 内全表扫描，成本随该 scope 切片数线性增长，
因此只在查询确实包含短词项时才运行。旧库分词器不匹配时，初始化会删除触发器和虚表，
按 trigram 重建并 `rebuild`，`chunks` 原文与稳定 ID 保持不变。

确定性 `ExperienceCard` 视图从每个候选来源切片中抽取显式标记的症状、根因、动作、失败尝试、
验证和条件行，并保留消息序号、原始字符区间、路径、符号与 outcome。它不调用模型、不执行文本中
出现的命令，也不把推断或自述升级为事实；没有显式槽位时保留完整来源切片作为唯一 evidence。
该视图在当前版本按需重建，没有独立数据库副本。检索以很小的确定性 prior 区分显式 bug/feature
问题，并只在查询明确询问失败经验时提升 failed-attempt 卡片；`reported_success` 来源获得极小
加分，`observed_failure` 来源轻微降权，但 prior 不会创建召回候选或覆盖 RRF，
因此尚不能解决语义相似但根因相反、版本冲突或大语料候选池漏召回。这是可解释的首个切片级基线，
不是完整的 episode/card 检索架构，必须由四档噪声代理集和实际 Coding Agent 对照实验验证。

可选 embedder 通过 `embed_batch` 注入，必须提供稳定 fingerprint/name 与 dimension；
所有向量必须数量、维度正确、有限且非零。默认 dense 实现为进程内、scope 内精确扫描
参考实现，最多接受 policy 中 `dense_scan_limit` 个 chunk；超过时显式报错。可选 Qdrant
ANN 后端按 fingerprint/维度分 collection，并在 ANN 查询前以原生 payload filter 限定
`user_id` 与 fingerprint。召回 ID 再经 SQLite 同 scope 读取校验，因此过期/孤儿点不会暴露
来源。SQLite 仍是事实来源；Qdrant 不可用时返回 typed error，不会静默回退。写入先幂等
upsert Qdrant，再提交 SQLite；失败写入可能留孤儿向量，但孤儿不会通过 SQL 校验，重试会覆盖
相同稳定 point ID。删除先删 SQLite，再清除远端所有 DMSA collections 的 scope 点；若远端
失败，SQL 已不可见数据，仍需重试删除以满足物理清理。SQLite 向量副本保留为离线精确参考与
恢复依据。

`corpus_state(user_ids)` 在单个 SQLite 只读事务中读取排序后的 scope epoch，并返回
`CorpusState(revision, fingerprint)`。epoch 在删除后继续单调递增，避免清空再写入产生 ABA
指纹。稳定的随机 corpus ID 也纳入 fingerprint，区分 revision 相同但来源不同的独立数据库；
全局 generation 同样纳入 fingerprint，因为 FTS5 的 BM25 全局统计会受其他 scope 写入影响。
离线配对回放应在两臂前后读取相同 scope 集合并确认 fingerprint 未变化。policy 的
`max_chars` 与 `max_tokens` 均是输出预算，权重必须有限且有界。

`estimate_tokens(text)` 用字符与 UTF-8 字节混合近似模型 token（CJK 约 1 字 1 token，
ASCII 与代码约 4 字节 1 token），覆盖中文、长标识符以及来源头部；它不是指定 tokenizer
的精确计数。检索与回放共用这一函数。单条证据无法完整装入剩余预算时，保留正文前缀并追加
`[truncated]` 标记，而不是整条丢弃；剩余空间不足以保留 160 字符可辨识正文时才跳过。

同一实例的读取与写入事务共享 RLock，搜索和幂等预检不能看到该连接的未提交数据。
embedding 在写事务之外执行，事务内再次检查幂等键。多个实例通过 SQLite WAL 和 busy timeout
协调。单实例数据库读取串行化，吞吐量及 1.8B token 规模尚未验证。
