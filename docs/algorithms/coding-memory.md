# Coding Memory 证据检索

`CodingMemory` 在用户 scope 内保存原始有序消息，并按消息切成最多 1800 字符的重叠
片段。每个片段保留原消息字符偏移、角色、请求、会话、路径、符号和保守 outcome。
出现错误/失败文本时标记 `observed_failure`；显式通过文本标记为
`reported_success`；其他内容标记 `unknown`。它不执行输入代码，也不把自述提升为验证成功。

SQLite WAL 事务同时写入幂等 request、原始消息、FTS5 切片和 scope revision。
幂等哈希包含 session 与完整、有序消息字段。相同重试返回原 revision，不同内容冲突。
FTS 候选在 SQL 中先过滤 user scope，再应用候选上限。精确路径/符号与 FTS 通道通过
稳定的 reciprocal-rank fusion (RRF) 合并；选取阶段按会话限额和字符预算控制重复。
证据文本包含 request/session/message 及字符 offset 引用。

可选 embedder 通过 `embed_batch` 注入，必须提供稳定 fingerprint/name 与 dimension；
所有向量必须数量、维度正确、有限且非零。当前 dense 实现为进程内、scope 内精确扫描
参考实现，最多接受 policy 中 `dense_scan_limit` 个 chunk；超过时显式报错。向量持久保存在
SQLite 中并绑定 fingerprint 与 dimension，不同 embedder 不会误读旧向量。这不是面向大规模
语料的 ANN 后端。

`corpus_state(user_ids)` 在单个 SQLite 只读事务中读取排序后的 scope epoch，并返回
`CorpusState(revision, fingerprint)`。epoch 在删除后继续单调递增，避免清空再写入产生 ABA
指纹。离线配对回放应在两臂前后读取相同 scope 集合并确认 fingerprint 未变化。policy 的
`max_chars` 与 `max_tokens` 均是输出预算，权重必须有限且有界。
