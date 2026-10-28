# DMSA 编码记忆与 RSI 融合深度优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 DMSA 记忆算法与 RSI 自进化合并为同一条可回放闭环，并补齐编码专用的代码锚点、任务结果反馈、代码快照失效检测能力。

**Architecture:** 记忆侧新增“代码锚点层”：从轨迹中确定性抽取 diff/定义/测试/堆栈锚点，独立建表并作为第四条召回通道，证据包显式携带结构化定位信息。进化侧扩展决策轨迹（语料指纹、策略版本、任务标识）与任务结果实体，使回放可以同时衡量召回、排序质量与上下文开销。仓库侧新增基于标准库 `ast` 的 Python 符号快照，用于标记已经失效的历史证据。

**Tech Stack:** Python 3.10+、标准库 `ast`/`sqlite3`/`re`、pytest、ruff、mypy(strict)。

## Global Constraints

- 每个 `.py` 文件不超过 300 行；超限必须拆分子模块。
- 中文 docstring + 英文变量名；新增类/常量/工具方法必须带中文注释。
- 严格 mypy（`strict = true`）与 ruff（`select = E,W,F,I,B,UP,SIM,D,C4,PT,RET,RUF`）。
- 错误必须 raise `DMSAError` 子类，禁止裸 `raise`。
- 禁止 `print()`，日志统一走 `dmsa.observability.logging`。
- 禁止 `import *`；禁止跨层直接 import 深层模块。
- 公共 API 改动必须有测试并同步 `docs/api-reference.md`。
- 算法改动同步更新 `docs/paper-mapping.md` 与 `docs/algorithms/*.md`。
- 路径使用 `pathlib.Path`；Windows 与 Linux/macOS 行为一致。
- 提交信息用 Conventional Commits；**本计划不自动执行 `git commit`**，提交动作由用户决定。

---

## File Structure

新增文件：

- `src/dmsa/memory/coding/code_anchors.py` — 锚点数据模型与确定性抽取（diff、代码块、定义、测试、堆栈）。
- `src/dmsa/memory/coding/anchor_store.py` — 锚点写入与 scope 内查询。
- `src/dmsa/memory/coding/snapshot.py` — 基于标准库 `ast` 的 Python 符号快照与文件指纹。
- `src/dmsa/gradient/task_results.py` — 任务结果实体与持久化。
- `tests/test_code_anchors.py` — 锚点抽取单测。
- `tests/test_code_anchor_retrieval.py` — 锚点通道与证据增强单测。
- `tests/test_task_results.py` — 任务结果与轨迹扩展单测。
- `tests/test_repo_snapshot.py` — 快照与失效标注单测。
- `benchmarks/coding/ablation.py` — 锚点通道开消融评测。

修改文件：

- `src/dmsa/memory/coding/schema.py` — 新增 `code_anchors`、`repo_snapshots` 表。
- `src/dmsa/memory/coding/models.py` — `SearchPolicy` 增加 `anchor_weight`。
- `src/dmsa/memory/coding/extraction.py` — 切片阶段同步产出锚点。
- `src/dmsa/memory/coding/storage_write.py` — 事务内写入锚点。
- `src/dmsa/memory/coding/storage_queries.py` — 锚点候选查询。
- `src/dmsa/memory/coding/storage.py` — 暴露锚点查询、快照读写，删除 scope 时清理锚点。
- `src/dmsa/memory/coding/retrieval.py` — 锚点召回通道与证据内容增强。
- `src/dmsa/gradient/feedback.py` — trace 扩展、任务结果接入、诊断种类扩展。
- `src/dmsa/gradient/replay_models.py` — NDCG 与聚合指标。
- `src/dmsa/gradient/replay_gate.py` — 多目标门禁。
- `src/dmsa/gradient/policy.py` — 新增 `identifier_confusion` 等候选提案。
- `src/dmsa/gradient/integrated.py` — 任务结果记录与活动策略复用。
- `src/dmsa/mcp_server/coding_memory.py` — 本地 stdio 接入活动策略、trace 与反馈工具。
- `src/dmsa/mcp_server/http/app.py`、`src/dmsa/mcp_server/http/feedback.py` — 契约与任务结果端点。
- `docs/api-reference.md`、`docs/paper-mapping.md`、`docs/algorithms/coding-memory.md`、`docs/algorithms/offline-rsi.md`、`docs/coding-memory-guide.md` — 文档同步。

---

### Task 1: 代码锚点抽取

**Files:**
- Create: `src/dmsa/memory/coding/code_anchors.py`
- Test: `tests/test_code_anchors.py`

**Interfaces:**
- Produces: `CodeAnchor(path: str, symbol: str, kind: str, confidence: float)`；`extract_anchors(text: str) -> tuple[CodeAnchor, ...]`；`anchor_terms(anchors) -> tuple[str, ...]`。

- [x] **Step 1: 写失败测试**

```python
from dmsa.memory.coding.code_anchors import extract_anchors


def test_extracts_diff_path_and_symbol():
    text = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1,3 +1,4 @@\n+    def handler():\n+        pass\n"
    anchors = extract_anchors(text)
    assert any(a.path == "src/app.py" and a.kind == "modifies" for a in anchors)
    assert any(a.symbol == "handler" and a.kind == "defines" for a in anchors)


def test_extracts_test_and_traceback():
    text = 'FAILED tests/test_app.py::test_handler - AssertionError\n  File "src/app.py", line 12, in handler\n'
    anchors = extract_anchors(text)
    assert any(a.kind == "test" and a.symbol == "tests/test_app.py::test_handler" for a in anchors)
    assert any(a.kind == "frame" and a.path == "src/app.py" and a.symbol == "handler" for a in anchors)
```

- [x] **Step 2: 运行 `pytest tests/test_code_anchors.py -v`，确认因模块不存在失败**
- [x] **Step 3: 实现 `code_anchors.py`**

抽取规则（全部确定性、无 LLM）：

| 证据形态 | 锚点 kind | 字段 |
|---|---|---|
| `diff --git`/`+++ b/path`/`--- a/path` | `modifies` | path |
| ` ```lang path=x ` 代码块 | `mentions` | path |
| `def name` / `class Name` | `defines` | symbol（类方法给出 `Class.name`） |
| `tests/x.py::test_y` | `test` | symbol 保留完整测试 id |
| `File "x.py", line N, in f` | `frame` | path + symbol |

- [x] **Step 4: 运行 `pytest tests/test_code_anchors.py -v`，确认通过**
- [x] **Step 5: 建议提交信息 `feat: add deterministic code anchor extraction`**

---

### Task 2: 锚点存储与召回通道

**Files:**
- Modify: `src/dmsa/memory/coding/schema.py`、`models.py`、`extraction.py`、`storage_write.py`、`storage_queries.py`、`storage.py`、`retrieval.py`
- Test: `tests/test_code_anchor_retrieval.py`

**Interfaces:**
- Consumes: `CodeAnchor`、`extract_anchors`（Task 1）。
- Produces: `SearchPolicy.anchor_weight: float = 0.6`；`CodingStore.anchor_candidates(terms, user_id, limit)`；证据内容新增 `涉及文件/符号/测试` 结构化行。

- [x] **Step 1: 写失败测试**（锚点命中的证据必须排在纯词面命中之前，且证据内出现锚点摘要）
- [x] **Step 2: 运行测试确认失败**
- [x] **Step 3: 加表**：`code_anchors(user_id, chunk_id, path, symbol, kind, confidence)`，索引 `(user_id, path)` 与 `(user_id, symbol)`；`repo_snapshots(user_id, root, created_at, payload)`
- [x] **Step 4: `Chunk` 增字段 `anchors: tuple[CodeAnchor, ...]`，`extract` 同步产出**
- [x] **Step 5: 写入事务内批量落锚点；`delete_scope` 同步删除 `code_anchors`、`repo_snapshots`**
- [x] **Step 6: 检索新增锚点通道**：以查询中的标识符/路径片段匹配锚点 path/symbol，RRF 权重 `policy.anchor_weight`；证据内容在来源头之后追加锚点摘要行
- [x] **Step 7: 运行 `pytest tests/test_code_anchor_retrieval.py tests/test_coding_memory.py tests/test_coding_retrieval_tuning.py -v` 全部通过**
- [x] **Step 8: 建议提交信息 `feat: index code anchors and add anchor retrieval channel`**

---

### Task 3: 决策轨迹与任务结果闭环

**Files:**
- Create: `src/dmsa/gradient/task_results.py`
- Modify: `src/dmsa/gradient/feedback.py`、`integrated.py`、`src/dmsa/mcp_server/http/app.py`、`http/feedback.py`
- Test: `tests/test_task_results.py`

**Interfaces:**
- Produces: `TaskResult(task_id, scope, trace_id, outcome, tests_passed, files_changed, tokens, latency_ms)`；`TaskResultStore.record(...)`、`.list(scope)`；`RetrievalFeedbackStore.trace(..., task_id=None, corpus_hash="")` 返回的 trace 记录语料指纹与策略版本。

- [x] **Step 1: 写失败测试**（任务结果按 scope 隔离、幂等、trace 关联可查）
- [x] **Step 2: 确认失败**
- [x] **Step 3: 实现 `task_results.py`，表 `rsi_task_results`**
- [x] **Step 4: trace 表增加 `task_id`、`corpus_hash`、`policy_version` 列（旧库用 `ALTER TABLE` 幂等补列）**
- [x] **Step 5: `IntegratedRSI.search(..., task_id=None)` 记录任务标识；新增 `record_task_result` 与 `task_results`**
- [x] **Step 6: HTTP 新增 `POST /task_result` 契约**
- [x] **Step 7: `pytest tests/test_task_results.py tests/test_integrated_rsi.py tests/test_coding_http.py -v` 通过**
- [x] **Step 8: 建议提交信息 `feat: extend decision trace and record task outcomes`**

---

### Task 4: 多目标回放门禁与策略空间

**Files:**
- Modify: `src/dmsa/gradient/replay_models.py`、`replay_gate.py`、`policy.py`、`feedback.py`、`http/feedback.py`
- Test: `tests/test_policy_replay.py`、`tests/test_integrated_rsi.py`

**Interfaces:**
- Produces: `CaseResult.ndcg`；`ReplayReport.mean_recall` / `mean_ndcg` / `total_tokens`；门禁规则 = 逐例召回不退化 **且** 平均 NDCG 提升 **且** token 总量不超过基线的 1.1 倍；新增诊断种类 `identifier_confusion`（提案 `anchor_weight += 0.2`）、`stale_evidence`（提案 `anchor_weight += 0.1` 且 `neighbor_window` 不变）。

- [x] **Step 1: 写失败测试**（排序改善但 token 暴涨必须拒绝；召回提升但排序变差必须拒绝）
- [x] **Step 2: 确认失败**
- [x] **Step 3: 实现 NDCG 与聚合指标**
- [x] **Step 4: 实现新门禁，保留 `GATE_VERSION` 升级为 `offline-rsi-gate-v2`**
- [x] **Step 5: 扩展 `propose_policy` 与允许的诊断种类**
- [x] **Step 6: `pytest tests/test_policy_replay.py tests/test_integrated_rsi.py -v` 通过**
- [x] **Step 7: 建议提交信息 `feat: enforce multi-objective replay gate`**

---

### Task 5: stdio MCP 统一策略与反馈工具

**Files:**
- Modify: `src/dmsa/mcp_server/coding_memory.py`
- Test: `tests/test_coding_memory_mcp.py`

**Interfaces:**
- Produces: 工具 `dmsa_coding_memory_feedback`（参数 `trace_id`、`outcome`、`kind`、`relevant_ids`）与 `dmsa_coding_memory_task_result`；search 结果附 `trace_id`。

- [x] **Step 1: 写失败测试**（search 返回 trace_id；feedback 写入后可被 evolve 读取；本地 stdio 仍关闭 dense）
- [x] **Step 2: 确认失败**
- [x] **Step 3: 本地 stdio 复用 `IntegratedRSI` 的活动策略，`dense_weight` 保持 0**
- [x] **Step 4: 实现两个新工具**
- [x] **Step 5: `pytest tests/test_coding_memory_mcp.py tests/test_stdio_integration.py -v` 通过**
- [x] **Step 6: 建议提交信息 `feat: unify stdio memory policy and add feedback tools`**

---

### Task 6: 仓库快照与失效标注

**Files:**
- Create: `src/dmsa/memory/coding/snapshot.py`
- Modify: `src/dmsa/memory/coding/storage.py`、`src/dmsa/memory/coding/retrieval.py`
- Test: `tests/test_repo_snapshot.py`

**Interfaces:**
- Produces: `RepoSnapshot(root, files, symbols)`、`build_snapshot(root, max_files=500)`、`stale_paths(snapshot) -> frozenset[str]`；scope 内保存快照后，检索证据中失效路径会被标注 `[stale]`。

- [x] **Step 1: 写失败测试**（符号被重命名后旧证据被标注失效；无快照时不标注）
- [x] **Step 2: 确认失败**
- [x] **Step 3: 用标准库 `ast` 解析 `.py`，提取模块/类/函数限定名、行号与文件 hash**
- [x] **Step 4: 快照存取与检索标注**
- [x] **Step 5: `pytest tests/test_repo_snapshot.py -v` 通过**
- [x] **Step 6: 建议提交信息 `feat: add repository symbol snapshot and stale evidence marking`**

---

### Task 7: 消融评测与文档同步

**Files:**
- Create: `benchmarks/coding/ablation.py`
- Modify: `docs/api-reference.md`、`docs/paper-mapping.md`、`docs/algorithms/coding-memory.md`、`docs/algorithms/offline-rsi.md`、`docs/coding-memory-guide.md`

**Interfaces:**
- Produces: `python -m benchmarks.coding.ablation` 输出锚点通道开/关的 recall、误导率与证据 token 对比。

- [x] **Step 1: 写评测脚本，输出真实数字（不预设结论）**
- [x] **Step 2: 运行 `python -m benchmarks.coding.ablation` 记录结果**
- [x] **Step 3: 更新五份文档**
- [x] **Step 4: 全量 `pytest -q` 覆盖率门禁 80% 通过**
- [x] **Step 5: 建议提交信息 `docs: sync api reference and algorithm notes for code anchors`**

---

## Self-Review

- 规格覆盖：方案中的代码锚点层、决策轨迹扩展、任务结果实体、多目标门禁、策略空间扩展、stdio 统一、仓库快照失效、消融评测与文档同步，均有对应任务。
- 占位符扫描：所有任务均给出具体文件、接口签名、测试与命令，无 TBD。
- 类型一致性：`CodeAnchor`、`TaskResult`、`RepoSnapshot`、`anchor_weight` 在各任务间命名一致。

## Execution Result (2026-10-27)

- 全部 7 个任务已实现；`pytest -q` 421 项通过，覆盖率 83.97%（门禁 80%）。
- `ruff check` 对本次新增与修改的文件全部通过；`mypy src` 仅剩 4 条既有基线错误
  （`gradient/clustering.py`、`dmsa/__init__.py`、`memory/serialization.py`，本次未改动）。
- 消融实测（`python -m benchmarks.coding.ablation`，2 任务自建结构化对照集）：
  `anchor_weight=0` 的 NDCG 为 0.815、`anchor_weight=2.0` 为 1.000，召回同为 1.0，
  证据字符数两臂相同（937.5）。结论仅限该开发难例，不代表官方成绩。
- 门禁版本升级为 `offline-rsi-gate-v2`：新增 NDCG 不退化、无收益不许变贵两项约束。
- 任务结果与检索反馈分表存放（`rsi_task_results`），当前只作为任务级门禁的证据，
  不参与策略激活；跨仓库 task-family 切分与真实 Agent 配对评测仍未完成。
