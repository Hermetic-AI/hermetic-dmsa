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
因此只在查询确实包含短词项时才运行。查询侧还会把蛇形、点分和驼峰标识符拆成片段，
例如 `missing_required_columns` 同时产生 `missing`、`required_columns` 与相邻组合，避免
查询里的组合名与源码中的函数名片段不一致时漏召回。旧库分词器不匹配时，初始化会删除
触发器和虚表，按 trigram 重建并 `rebuild`，`chunks` 原文与稳定 ID 保持不变。

`coding.ranking` 在通道融合之后提供确定性的证据质量先验，只调整已有候选的分数，不创建新候选、不覆盖 RRF。查询特征包括三词短语、标识符片段、去掉单字噪声后的 CJK
三元组、文件路径和关键词。证据侧按以下信号加减分：行首代码结构（只认 `def/class/return`
等代码行，避免英文散文里的 `if/for` 被当成代码）；代码块 `path=` 命中查询中的文件时
视为可直接采用的补丁；同时出现代码与显式验证（`pytest ... pass`、`全部通过`）时加分；
证据符号片段覆盖查询关键词时加分；`旧会话`、`历史修复尝试`、`未通过验证` 等失败尝试标记
降权；长中文重复填充文本在没有代码时按低信息密度降权。历史 `[tool_use Write]` 整文件快照
是旧版本镜像，排序时降权并在证据装包阶段直接跳过，宁可不返回也不把过期文件内容注入当前仓库。
这些先验只影响排序与装包，不改变召回通道；其效果仍需代理噪声集与真实 Coding Agent 配对任务
测量，不能当作论文实验结论。

邻居扩展与关系扩展的分数按折扣系数下压，保证扩展证据永远排在它的父命中之后。折扣作用在
证据质量调分之后的最终分上，而该分数可能是负值（先验扣分超过通道 RRF 贡献时，常见于没有
代码结构的纯散文或日志片段）：直接乘折扣会把负分放大成更接近零的数，使扩展证据反超父命中，
把真正命中查询词的切片挤到后面。因此实际取 `min(父分 × 折扣, 父分 − EXPANSION_EPSILON)`，
间隔常量取 `1e-9`，在双精度下足够区分且不影响正分场景原有的折扣比例。

`coding.episodes` 在写入时构造会话级经验卡：把同一 session 的多轮消息按写入顺序合并，抽取
症状、根因、失败尝试次数、最终补丁（路径与正文）以及补丁之后出现的显式验证结论，形成一条
`[episode]` 开头的独立证据切片。经验卡只在“存在补丁且其后有显式验证”时生成，未验证的尝试
不会被复制成经验；同一 session 只保留最新一张，旧卡片与其精确词项一起删除。原始消息切片不受
影响，仍作为可追溯的原始证据。经验卡不调用模型、不执行文本中的命令，也不把自述升级为已验证
事实；它只是把已存在于会话里的显式内容压缩成可复用的工程经验视图。

`coding.packing` 负责证据挑选：`top_k` 只作为上限，实际返回受 `evidence_limit`、分数下限
`score_floor_ratio`、最小条数 `min_evidence`、单会话 `per_session_limit` 与单路径
`per_path_limit` 共同约束，再叠加词项近重过滤与字符/token 预算。该设计的目标是让 Agent 一次
只看到少量高相关证据，而不是把整段会话塞进上下文；真实平台快照回放中，返回条数从约 32 条降到
12 条，目标函数仍排在第 1–2 位。

检索与写入都把外部网络调用排除在数据库锁之外：查询向量化与远端向量检索发生在锁外，SQLite
读写各占一个短临界区；向量发布也移到事务之前，因此慢上游不会阻塞同进程的其他 Add/Search。
代价是稠密通道与库内快照之间存在极小的时序窗口，SQLite 仍是事实来源，召回 ID 仍需经同 scope
校验。

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

## 代码锚点层

`coding.code_anchors` 从轨迹原文里确定性地抽取"代码结构事实"，不做语义推断：

| 证据形态 | 锚点 kind | 字段 |
|---|---|---|
| `diff --git` 与 `+++ b/path` | `modifies` | path |
| `--- a/path` | `mentions` | path |
| ` ```lang path=x ` 代码块 | `mentions` | path |
| `class` / `def`（按缩进归属到类） | `defines` | 限定符号及其逐级缩写 |
| `tests/x.py::test_y` | `test` | 完整测试 id |
| `File "x.py", line N, in f` | `frame` | path 与函数名 |

补丁行先还原为真实代码行（只保留新增与上下文行，删除行不参与判定），因此 `+class`、
`+    def` 这类带补丁前缀的声明同样能被识别为定义。每个切片的锚点写入 `code_anchors` 表，
按 `(user_id, term)` 建索引；`SearchPolicy.anchor_weight`（默认 `0.6`，范围 `[0, 10]`）
控制该通道的 RRF 权重，设为 `0` 可在配对回放里关闭锚点召回而不影响其它通道。

证据文本在来源头之后追加一行 `[anchors] path::symbol; ...`，把"这段历史到底改了什么"
变成结构化可消费信息，而不是让 Agent 自己从散文中推断。

## 仓库快照与失效标注

`coding.snapshot` 用标准库 `ast` 扫描工作区的 Python 文件，产出仓库相对路径、文件内容
指纹与限定符号（如 `Handler.handle_request`）。`CodingMemory.save_repo_snapshot(user_id, root)`
把快照写入 scope；检索时据此判断历史锚点是否仍然成立：路径在快照中消失、或限定符号与其
末段名称都找不到时，证据被标注 `[stale]` 并轻微降权，但原文仍然返回，避免把"过期"当成
"无用"。没有快照时不做任何失效判断，因此竞赛与离线评测路径不受影响。

快照只描述 Python 源码事实，不推断调用关系、数据流或跨语言依赖；非 Python 文件只记录
路径与指纹，不参与符号判定。

## 代码骨架与工程关系

`coding.skeleton.extract_skeleton(text, depth)` 从证据正文里确定性地抽出代码结构行
（`class` / `def` / `import` / 装饰器等，最多 `depth` 行），证据装包时以 `[skeleton]`
段追加在来源头之后。它只做行级识别，不解析语义、不改写正文；`SearchPolicy.skeleton_depth`
（默认 `6`，范围 `[0, 40]`）设为 `0` 即完全关闭该段，因此可以在配对回放里单独测量它的收益。

`code_relations(user_id, chunk_id, rel_type, target)` 把锚点转成工程关系边：`defines` 锚点
产生 `defines_symbol`，`test` 锚点产生 `validates_test`，路径锚点产生 `touches_file`。
`SearchPolicy.relation_window`（默认 `0`，范围 `[0, 20]`）开启后，装包阶段会对前
`relation_window` 条已选证据做一次同 target 的关系扩展（"改了同一文件 / 定义了同一符号 /
被同一测试覆盖"的历史切片），扩展项分数按 `RELATION_DISCOUNT` 折扣，保证排在原始命中之后。
关系边只来自轨迹原文里的显式锚点，不做调用图或数据流推断。

## 仓库维度分区

`scope_key(user_id, repo)` 返回 `"user#repo"`（`repo` 为空时退化为 `user_id`），该复合键是
写入、检索、向量、锚点、关系、快照、语料指纹与 RSI 轨迹共用的唯一分区键，因此不同仓库的
证据不会互相召回，也不会互相污染回放样本。`repo` 在所有接口上都是可选增量参数，缺省行为与
旧版本完全一致。

分隔符 `#` 不允许出现在 `user_id` 或 `repo` 中：一旦允许，`a#b` 就无法反向归属，按用户清理
数据时会漏删或误删相邻子域，`scope_key` 会直接抛 `CodingMemoryValidationError`。

`delete_scope(user_id)` 不带 `repo` 时清理该用户的主 scope 与全部仓库子域（子域清单来自
`scope_partitions`），带 `repo` 时只清理该分区；向量索引按同一批 scope 逐个删除。
`scope_epochs` 中的已清空 scope 会保留 epoch，以维持语料指纹单调、避免"清空后重写"产生
ABA 指纹。`save_repo_snapshot(user_id, root, repo=...)` 写入与检索相同的分区，因此仓库级
失效标注只对该仓库的历史证据生效。

## 扩展能力

写入门控与遗忘淘汰、条目信任模型与因果归因、端到端有效性度量、程序性记忆、团队作用域
与写冲突仲裁、多跳迭代检索与召回解释、记忆合并与跨仓库迁移、稠密混合检索与重排、时序
as-of 查询各自独立成篇，见 [Coding Memory 扩展能力](coding-memory-extensions.md)。
