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
稳定的 reciprocal-rank fusion (RRF) 合并；选取阶段按会话限额和字符预算控制重复。
证据文本包含 request/session/message 及字符 offset 引用。

确定性 `ExperienceCard` 视图从每个候选来源切片中抽取显式标记的症状、根因、动作、失败尝试、
验证和条件行，并保留消息序号、原始字符区间、路径、符号与 outcome。它不调用模型、不执行文本中
出现的命令，也不把推断或自述升级为事实；没有显式槽位时保留完整来源切片作为唯一 evidence。
该视图在当前版本按需重建，没有独立数据库副本。检索以很小的确定性 prior 区分显式 bug/feature
问题，并只在查询明确询问失败经验时提升 failed-attempt 卡片；prior 不会创建召回候选或覆盖 RRF，
因此尚不能解决语义相似但根因相反、版本冲突或大语料候选池漏召回。这是可解释的首个切片级基线，
不是完整的 episode/card 检索架构，必须由四档噪声代理集和实际 Coding Agent 对照实验验证。

可选 embedder 通过 `embed_batch` 注入，必须提供稳定 fingerprint/name 与 dimension；
所有向量必须数量、维度正确、有限且非零。当前 dense 实现为进程内、scope 内精确扫描
参考实现，最多接受 policy 中 `dense_scan_limit` 个 chunk；超过时显式报错。向量持久保存在
SQLite 中并绑定 fingerprint 与 dimension，不同 embedder 不会误读旧向量。这不是面向大规模
语料的 ANN 后端。

`corpus_state(user_ids)` 在单个 SQLite 只读事务中读取排序后的 scope epoch，并返回
`CorpusState(revision, fingerprint)`。epoch 在删除后继续单调递增，避免清空再写入产生 ABA
指纹。稳定的随机 corpus ID 也纳入 fingerprint，区分 revision 相同但来源不同的独立数据库；
全局 generation 同样纳入 fingerprint，因为 FTS5 的 BM25 全局统计会受其他 scope 写入影响。
离线配对回放应在两臂前后读取相同 scope 集合并确认 fingerprint 未变化。policy 的
`max_chars` 与 `max_tokens` 均是输出预算，权重必须有限且有界。

`estimate_tokens(text)` 当前按 UTF-8 字节计数，作为文本 token 的保守预算代理，覆盖中文、
长标识符以及来源头部；并非指定模型 tokenizer 的精确计数。检索与回放共用这一函数。
单条证据无法完整装入剩余预算时跳过，不返回截断后失真的来源片段。

同一实例的读取与写入事务共享 RLock，搜索和幂等预检不能看到该连接的未提交数据。
embedding 在写事务之外执行，事务内再次检查幂等键。多个实例通过 SQLite WAL 和 busy timeout
协调。单实例数据库读取串行化，吞吐量及 1.8B token 规模尚未验证。
