# Coding Memory HTTP (development)

安装 `.[http]`、设置 `DMSA_MEMORY_API_KEY`，执行
`dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3`。这是仅有
稀疏索引的本地开发模式。competition profile 还要求
`DMSA_EMBEDDING_BASE_URL`、`DMSA_EMBEDDING_API_KEY` 和
`DMSA_EMBEDDING_MODEL=text-embedding-v4`；不会默认推断 provider 支持该模型。
详见 `docs/api-reference.md`。

# hermetic-dmsa 使用文档

> **面向读者**：把 hermetic-dmsa 接入 Claude Code / OpenCode / Hermes Agent 的**最终用户**。
>
> 这份文档回答两个问题：
> 1. **怎么用**——3 方集成的具体步骤
> 2. **怎么验证使用生效**——使用过程检测方法（CLI 工具 + 实时 metrics + 日志）

---

## 目录

- [1. 30 秒快速开始](#1-30-秒快速开始)
- [2. 三方集成接入步骤](#2-三方集成接入步骤)
- [3. 验证使用是否生效](#3-验证使用是否生效)
- [4. 使用过程检测](#4-使用过程检测)
- [5. 常见问题排查](#5-常见问题排查)

---

## 1. 30 秒快速开始

### 1.1 安装 hermetic-dmsa

```bash
cd hermetic-dmsa
pip install ./vendor/pmd-0.1.0.tar.gz     # 随附的固定 PMD 源码包
pip install -e ".[all]"                     # hermetic-dmsa 本体 + dev 依赖
```

> PMD 的固定源码包随 checkout 提供；先安装 vendor 包，再安装本项目。
> 来源与升级方式见 [分发与部署说明](deployment.md)，不要用未经核实的同名 PyPI 包替换。

确认 `dmsa-mcp-server` 和 `dmsa-inspect` 在 PATH 中：

```bash
$ which dmsa-mcp-server
/path/to/hermetic-dmsa/.venv/bin/dmsa-mcp-server

$ which dmsa-inspect
/path/to/hermetic-dmsa/.venv/bin/dmsa-inspect
```

> ⚠️ **已知安装问题（已修复）**：
> - PMD skeleton 的旧 pyproject.toml 用了 `[project.optional-dependencies.dev]` 这种嵌套语法，**新 setuptools 报错**（`dev must be array`）。**已修复**（统一放进 `optional-dependencies.dev = [...]`）。
> - hermetic-dmsa 原本用 setuptools_scm 动态版本，**新 setuptools_scm 8 不支持 `version_scheme=post`**。**已修复**（改为静态 version；后续 tag release 时再切回 scm）。

### 1.2 跑端到端 demo（无需任何 Agent）

```bash
cd hermetic-dmsa
PYTHONPATH=src python examples/01_quickstart_mcp.py
```

输出示例（Windows 终端）：

```
============================================================
  hermetic-dmsa 端到端 demo
============================================================
  本 demo 演示完整的 'write -> call -> fail -> feedback -> aggregate' 循环。

============================================================
  Step 1: 写入 user 偏好
============================================================
  [OK] add_memory OK, node_id = 01HXYZ...

============================================================
  Step 2: 模拟 tool call 失败
============================================================
  [OK] tool_call 记录: get_date
  [OK] critique category: schema
  [OK] critique: tool 'get_date' failed due to format/schema error: format must be YYYY-MM-DD

============================================================
  Step 3: 用户反馈 -> SGA 聚合
============================================================
  [OK] feedback 处理完成: 1 个簇
  [OK] fused gradient 置信度: 0.00
  [OK] has_conflict: False
...
```

✅ **Demo 成功 = 安装 OK，hermetic-dmsa 在你的环境里能跑通完整循环**。

---

## 2. 三方集成接入步骤

### 2.1 Claude Code（最简单 — 5 分钟）

#### 步骤 1：创建 `.mcp.json`

在**项目根目录**创建 `.mcp.json`（或写入 `~/.claude.json` 全局配置）：

```json
{
  "mcpServers": {
    "hermetic-dmsa": {
      "command": "dmsa-mcp-server",
      "args": ["--storage", "~/.hermetic-dmsa/db.sqlite"]
    }
  }
}
```

#### 步骤 2：重启 Claude Code

完全退出 Claude Code，重新打开。

#### 步骤 3：验证接入

在 Claude Code 对话中输入：

> 列出你可见的 MCP 工具

应该看到 7 个 `dmsa_*` 工具：

| 工具名 | 用途 |
|---|---|
| `dmsa_add_memory` | 写入带 creator_prompt_id 的 memory |
| `dmsa_query_lineage` | 查询某个节点的祖先/后代链 |
| `dmsa_feedback` | 用户反馈 → 触发 SGA 聚合 |
| `dmsa_apply_gradient` | 把梯度写回可微记忆节点（v0.1 支持 prompt 目标；schema 目标请直接用 `dmsa.toolbp.ToolBPEngine`） |
| `dmsa_tool_call` | 包装工具调用 + 自动生成 critique |
| `dmsa_snapshot` | 打点版本（可回滚） |
| `dmsa_search` | lineage-aware 检索 |

✅ **看到 7 个工具 = 接入成功**。

> 详细文档：[`integrations/claude-code/README.md`](../integrations/claude-code/README.md)

---

### 2.2 OpenCode（5 分钟）

#### 步骤 1：创建 `opencode.json`

```json
{
  "mcp": {
    "hermetic-dmsa": {
      "type": "stdio",
      "command": "dmsa-mcp-server",
      "args": ["--storage", "~/.hermetic-dmsa/db.sqlite"]
    }
  }
}
```

放在项目根目录或 `~/.config/opencode/opencode.json`。

#### 步骤 2：重启 OpenCode

#### 步骤 3：验证

在 OpenCode 对话中输入：

> 列出所有 MCP 工具

✅ **看到 7 个 `dmsa_*` 工具 = 接入成功**。

> 详细文档：[`integrations/opencode/README.md`](../integrations/opencode/README.md)

---

### 2.3 Hermes Agent（15 分钟 — Skill 形式）

#### 步骤 1：复制 skill 到 Hermes skills 目录

```bash
mkdir -p ~/.hermes/skills/hermetic-dmsa
cp integrations/hermes/SKILL.md ~/.hermes/skills/hermetic-dmsa/
```

#### 步骤 2：启动 dmsa-mcp-server 作为后台进程

```bash
dmsa-mcp-server --storage ~/.hermetic-dmsa/hermes.sqlite &
```

生产环境推荐用 systemd / launchd 守护。

#### 步骤 3：Hermes 自动识别

Hermes 会扫描 `~/.hermes/skills/`，识别 `hermetic-dmsa` skill，并通过 SKILL.md 描述知道何时调用。

✅ **Hermes 在对话中提到 dmsa 工具 = 接入成功**。

> 详细文档：[`integrations/hermes/README.md`](../integrations/hermes/README.md)

---

## 3. 验证使用是否生效

> 这是核心 — 怎么知道 hermetic-dmsa 真的在记录 + 在反向传播 + 在聚合？

提供 **3 层验证**（从浅到深）：

### 3.1 第 1 层：CLI 快速检查（5 秒）

启动 MCP server 后，**另开一个终端**跑：

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite dag
```

输出（文本模式）：

```
============================================================
  hermetic-dmsa DAG Inspection
============================================================
  Storage: /home/user/.hermetic-dmsa/db.sqlite
  Nodes:   3
  Edges:   1

  -- Node Types --
    TEXT                 3
  -- Status --
    ACTIVE               3
  -- Top Tags --
    user_pref            1
    derived              1
  -- Top Creator Agents --
    agent1               2
  First node: 2026-07-03T10:15:30
  Last node:  2026-07-03T10:23:45
============================================================
```

✅ **Node count > 0 = Agent 真的在写入记忆**。
✅ **Edges > 0 = lineage 关系存在**。

要 JSON 格式（机器可读 / pipeline）：

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite --json dag | jq .node_count
```

### 3.2 第 2 层：检查单个节点详情（10 秒）

先获取一个 node ID：

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite dag
# 输出中 --top creator agents 部分有 agent_id，但没有 node_id
# 用 JSON 拿 node_id：
```

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite --json dag | jq .creator_agents
# 然后查询 pmd 的反向接口，或用：
```

更直接：列出所有 node ID + content：

```bash
# 用 sqlite3 直接查（需要 sqlite3 CLI）
sqlite3 ~/.hermetic-dmsa/db.sqlite "SELECT id, substr(content, 1, 50), tags FROM memory_nodes LIMIT 5"
```

拿到 node_id 后：

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite node --node-id <node_id>
```

输出：

```
============================================================
  hermetic-dmsa Node Inspection
============================================================
  ID:            01HXYZABC...
  Version:       1
  Content type:  TEXT
  Status:        ACTIVE
  Importance:    0.9
  Tags:          ["user_pref"]
  requires_grad: True

  Content:
    user prefers dark mode in all UIs

  Creator prompt_id: p-abc...
  Creator agent_id:  agent-1
  Creator PromptContext:
    model:        gpt-4o
    temperature:  0.7
    system_prompt: you are a UI assistant
    user_prompt:   remember: I like dark mode

  Parents edges:   0
  Children edges:  1
  Recent gradients: 1
  Recent gradients:
    - [USER] be more verbose
============================================================
```

✅ **Creator PromptContext 不为空 = creator 链完整**（这是论文创新点 #1）。
✅ **requires_grad=True = 该记忆可被反向传播影响**（论文创新点 #2）。
✅ **Recent gradients > 0 = 至少发生过一次反向传播**（论文创新点 #3）。

### 3.3 第 3 层：检查反向传播 metrics（10 秒）

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite metrics
```

输出：

```
============================================================
  hermetic-dmsa Backward Gradient Metrics
============================================================
  Total gradients:  12
  Token cost total: 480
  -- By Feedback Source --
    HEURISTIC            8
    USER                 3
    AUTOMATED_TEST       1
  -- Recent Gradients (last 20) --
    [1719995400] HEURISTIC   tokens=  40  format must be YYYY-MM-DD...
    [1719995350] USER        tokens=  35  should be more specific...
    ...
============================================================
```

✅ **Total gradients > 0 = 反向传播发生过**。
✅ **Token cost total 反映实际工作量**。

### 3.4 验证清单（5 项必查）

| 检查项 | 命令 | 期望 |
|---|---|---|
| 安装正确 | `which dmsa-mcp-server` | 输出路径 |
| DAG 在累积数据 | `dmsa-inspect dag` | `Nodes > 0` |
| Creator 链完整 | `dmsa-inspect node --node-id <id>` | `creator_prompt.model != ""` |
| 反向传播发生过 | `dmsa-inspect metrics` | `Total gradients > 0` |
| 版本可回滚 | `dmsa-inspect versions` | `snapshots.length > 0`（snapshot 后才有）|

---

## 4. 使用过程检测

> 区别于 §3 的「事后验证」，这里是**运行时实时监控**。

### 4.1 实时 metrics（运行时导出）

`dmsa.observability.metrics` 模块暴露 `Counter` 和 `Histogram`：

```python
from dmsa.observability import get_registry

metrics = get_registry()
print(metrics.export())
```

输出：

```python
{
  "counters": [
    {"type": "counter", "name": "dmsa.add_memory", "value": 42.0},
    {"type": "counter", "name": "dmsa.add_edge", "value": 15.0},
    {"type": "counter", "name": "dmsa.aggregate.runs", "value": 8.0},
    {"type": "counter", "name": "dmsa.toolbp.project", "value": 3.0},
  ],
  "histograms": [
    {
      "type": "histogram",
      "name": "dmsa.aggregate.samples",
      "count": 8,
      "sum": 32.0,
      "mean": 4.0,
      "buckets": {"0.001": 0, "0.01": 0, ..., "inf": 8},
    },
  ],
}
```

**实时监控仪表板示例**（30 行 Python）：

```python
import time
from dmsa.observability import get_registry

while True:
    metrics = get_registry().export()
    add_count = next(c["value"] for c in metrics["counters"] if c["name"] == "dmsa.add_memory")
    aggregate_runs = next(c["value"] for c in metrics["counters"] if c["name"] == "dmsa.aggregate.runs")
    project_count = next(c["value"] for c in metrics["counters"] if c["name"] == "dmsa.toolbp.project")

    print(f"[{time.strftime('%H:%M:%S')}] "
          f"add_memory={add_count} aggregate={aggregate_runs} project={project_count}")

    time.sleep(1)
```

输出（每秒刷新）：

```
[10:30:15] add_memory=42 aggregate=8 project=3
[10:30:16] add_memory=43 aggregate=8 project=3
[10:30:17] add_memory=43 aggregate=9 project=4
```

### 4.2 Trace span（追踪单个操作链路）

`dmsa.observability.trace` 暴露 `start_span` context manager：

```python
from dmsa.observability import start_span, get_current_span

with start_span("user_session", user_id="alice"):
    # ... 用户的 MCP 调用 ...
    # 每一次 add_memory / backward / aggregate 都会嵌套 span
    pass

# 退出后查看当前 span 树
span = get_current_span()
print(span.to_dict())
```

输出示例：

```python
{
  "span_id": "abc123...",
  "name": "user_session",
  "duration_seconds": 5.42,
  "attributes": {"user_id": "alice"},
  "children": [
    {
      "span_id": "def456...",
      "name": "dmsa.add_memory",
      "duration_seconds": 0.012,
      "attributes": {"node_id": "01HXYZ...", "content_length": 42},
      "children": []
    },
    {
      "span_id": "ghi789...",
      "name": "dmsa.aggregate",
      "duration_seconds": 0.085,
      "attributes": {"sample_count": 3, "cluster_count": 1},
      "children": []
    }
  ]
}
```

### 4.3 结构化 JSON 日志

`dmsa.observability.logging.configure_root()` 启用 JSON 输出：

```python
from dmsa.observability.logging import configure_root
import logging

configure_root(level=logging.INFO)
```

之后所有 dmsa 操作都会输出 JSON 行到 stderr：

```json
{"ts": "2026-07-03T10:30:15.123Z", "level": "INFO", "logger": "dmsa.memory.layer", "message": "memory added", "node_id": "01HXYZ...", "content_length": 42, "requires_grad": false}
{"ts": "2026-07-03T10:30:15.456Z", "level": "INFO", "logger": "dmsa.gradient.aggregator", "message": "aggregation complete", "sample_count": 3, "cluster_count": 1, "conflict_count": 0}
{"ts": "2026-07-03T10:30:15.890Z", "level": "INFO", "logger": "dmsa.toolbp.projector", "message": "tool schema updated", "tool_name": "get_date", "old_version": 1, "new_version": 2, "ops_count": 1}
```

**生产用法**：把 stderr 重定向到文件 + 用 `jq` 实时分析：

```bash
dmsa-mcp-server --storage ~/.hermetic-dmsa/db.sqlite 2>>~/.hermetic-dmsa/server.log &
# 实时看错误：
tail -f ~/.hermetic-dmsa/server.log | jq 'select(.level=="ERROR")'
# 统计调用频率：
tail -f ~/.hermetic-dmsa/server.log | jq -r '"\(.ts) \(.message)"' | head -100
```

### 4.4 MCP server 自检（直接调 JSON-RPC）

起 server 后用 Python 直接发请求：

```python
import subprocess
import json

# 启动 server 作为子进程
proc = subprocess.Popen(
    ["dmsa-mcp-server", "--storage", "/tmp/test.sqlite"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

# 发 initialize
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}) + "\n")
proc.stdin.flush()
print(proc.stdout.readline())  # 应返回 server info

# 发 tools/list
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}) + "\n")
proc.stdin.flush()
print(proc.stdout.readline())  # 应返回 7 个工具

proc.terminate()
```

### 4.5 监控告警建议

生产部署时建议监控这些指标：

| 指标 | 告警阈值 |
|---|---|
| `dmsa.add_memory` 速率 | < 0.1/s 持续 5 分钟 → 可能 Agent 闲置 |
| `dmsa.aggregate.samples` p99 | > 100 → 触发 OOM 风险 |
| `dmsa.toolbp.project` 频率 | > 10/min → schema 频繁更新需要审查 |
| `backward_gradients.total` | 持续 0 → 反向传播未配置成功 |
| `pmd.exceptions` 日志 | 任何 ERROR → 立即告警 |

---

## 5. 常见问题排查

### Q1: Claude Code 看不到 `dmsa_*` 工具

**检查**：
1. `.mcp.json` 语法对吗？（`cat .mcp.json | jq .` 应能解析）
2. `dmsa-mcp-server` 在 PATH 中吗？（`which dmsa-mcp-server`）
3. 重启 Claude Code 了吗？
4. 看 Claude Code 日志（通常在 `~/.claude/logs/`）

### Q2: 工具调用失败 `-32001`

这是 `DMSAError`，看 stderr：

```bash
dmsa-mcp-server --storage /tmp/test.sqlite 2>&1 | tee server.log
```

### Q3: 反向传播没生效

检查：

```bash
dmsa-inspect --storage ~/.hermetic-dmsa/db.sqlite metrics
```

- `Total gradients == 0` → Agent 没调过 `dmsa_feedback`
- `Total gradients > 0` 但 `Token cost total == 0` → StaticBackwardDeriver（正常，零成本）
- `Total gradients > 1000/min` → 过度反馈，考虑调整 severity 阈值

### Q4: 数据库文件很大

`dmsa-inspect --json dag | jq .node_count` 检查节点数。如 > 100k：

```bash
# 1. 打点版本
dmsa-mcp-server ... # 通过 dmsa_snapshot 工具

# 2. 手动 VACUUM
sqlite3 ~/.hermetic-dmsa/db.sqlite "VACUUM"

# 3. 或归档老节点（PMD 的 DELETED 状态会保留，但不会再被检索）
# 用 dmsa_snapshot + dmsa_rollback 配合清理（v0.2 将提供 dmsa_purge 工具）
```

### Q5: 性能调优

- `--embedder-dim` 调大（如 256）→ 检索更准但更慢
- `--embedder-dim` 调小（如 32）→ 快但不准
- 默认 `clustering_backend=fallback`，单链接；装 `hdbscan` 后自动切到 HDBSCAN

```bash
pip install hermetic-dmsa[clustering]  # 装 HDBSCAN
# 设置环境变量：
export DMSA_GRADIENT__CLUSTERING_BACKEND=hdbscan
```

### Q6: 默认的嵌入/融合没有语义，怎么接真实 LLM？

默认 `HashEmbedder`（哈希嵌入）+ `StaticFusion`（文本拼接）是零依赖参考实现，
适合测试和演示，但**没有真实语义**。生产环境切换到真实 LLM 后端：

```bash
pip install "hermetic-dmsa[llm]"   # 装 openai
export OPENAI_API_KEY=sk-...

# 真实语义嵌入（检索、聚类都会变准）
export DMSA_EMBEDDER__BACKEND=openai
# 可选：换模型 / 走 OpenAI 兼容 endpoint（vLLM、代理等）
export DMSA_EMBEDDER__MODEL=text-embedding-3-small

# LLM 融合（fused_text 由 LLM 改写，去重 + 消解措辞冲突）
export DMSA_GRADIENT__FUSION_BACKEND=llm
export DMSA_GRADIENT__LLM_CHAT_MODEL=gpt-4o-mini
export DMSA_GRADIENT__LLM_BASE_URL=          # 可选，兼容 endpoint
```

代码层面（不走 MCP server 时）直接注入：

```python
from dmsa.gradient import LLMFusion, OpenAIEmbedder, SGAAggregator, StaticFusion

aggregator = SGAAggregator(
    embedder=OpenAIEmbedder(),
    fusion=LLMFusion(fallback=StaticFusion()),  # API 挂了就降级拼接
)
```

---

## 下一步

- **开发者文档**：[`docs/architecture.md`](architecture.md) — 5 层架构图 + 数据流
- **论文对应**：[`docs/paper-mapping.md`](paper-mapping.md) — 每个模块对应论文哪一章
- **三方集成详细**：[`docs/integrations.md`](integrations.md)
- **API reference**（待生成）：运行 `mkdocs build` 生成
# Coding Memory HTTP (development)

Install with `pip install -e '.[http]'`, set `DMSA_MEMORY_API_KEY`, then run
`dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3`. This local
profile uses sparse retrieval without embeddings and is suitable for protocol and
workflow development only. The `competition` profile requires
`DMSA_EMBEDDING_BASE_URL`, `DMSA_EMBEDDING_API_KEY`,
`DMSA_EMBEDDING_MODEL=text-embedding-v4`; it sends paid/provider requests only
when clients call `/add` or `/search`. No provider is assumed to offer this model by
default. See `docs/api-reference.md` for request schemas and response shapes.
