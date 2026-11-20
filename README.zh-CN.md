<div align="center">

# hermetic-dmsa

**面向 Coding Agent 的溯源记忆层——存下发生过的事，取回当前适用的证据，并离线改进检索策略。**

[![CI](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/ci.yml/badge.svg)](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/ci.yml)
[![Docs](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/docs.yml/badge.svg)](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/docs.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Typing: mypy strict](https://img.shields.io/badge/typing-mypy%20strict-2a6db2.svg)](https://mypy-lang.org/)

[English](README.md) | [简体中文](README.zh-CN.md)

</div>

---

`hermetic-dmsa` 给 Coding Agent 一份关于当前仓库的长期记忆：哪里出过问题、问题在哪、
为什么那样修、怎么验证的。它保存原始历史，在明确的预算内返回可溯源的证据，并能在
开发集上提出、门禁化地改进自身的检索策略——改进只发生在离线，且每次被接受的改动都要
在冻结语料上回放验证。

它同时是一个库、一个 MCP server 和一个 HTTP 服务。它不改代码、不跑测试、不评判补丁。

## 为什么做这个

检索增强的 Coding Agent 有一种特定的失败模式：取回的历史证据**相似**，但**不适用**。
一个修好旧版本函数的补丁，看起来正好能解决今天的任务，却会弄坏构建；一份提到正确
符号的失败日志，根因却完全不对；冗长的原始日志把唯一关键的那行挤了出去。

`hermetic-dmsa` 就是围绕这个问题设计的：

| 能力 | 具体做什么 |
|---|---|
| **保存原文** | 原始消息、角色、时间戳与字符偏移逐字持久化，始终是最终事实来源。 |
| **找对适用的** | 精确路径/符号匹配、FTS5 全文检索和可选稠密通道经 RRF 融合，再按 scope、条件与版本过滤。 |
| **出示来源** | 每条返回切片都带溯源头；经验卡是对原文的确定性视图，不是编造的摘要。 |
| **承认不确定** | 「自述成功」就只是自述成功。测试证据只证明那个测试，不证明补丁正确。 |
| **离线改进** | 检索策略的改动先提案，再在配对回放中验证，只有冻结回放门禁确认无退步才会被接受。 |

## 架构

```text
┌──────────────────────────────────────────────────────────────────┐
│ L5  Integrations — Claude Code · OpenCode · Codex · Hermes      │
└──────────────────────────────────────────────────────────────────┘
                                │ stdio JSON-RPC 2.0  /  HTTP
┌──────────────────────────────────────────────────────────────────┐
│ L4  MCP Server (dmsa.mcp_server)                                │
│     7 个原有工具 + 14 个 dmsa_coding_memory_* 工具                │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L3  Tool-Use 反向传播 (dmsa.toolbp)                             │
│     JSON Schema AST 投影 + 带校验的编辑操作                       │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L2  语义梯度聚合 (dmsa.gradient)                                │
│     SGA 聚类 · 策略演化 · 配对回放 · 门禁                         │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L1  记忆层 (dmsa.memory)                                         │
│     Coding Memory 引擎 + PMD provenance DAG                      │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L0  pmd — 随仓库提供、哈希锁定的源码快照                          │
└──────────────────────────────────────────────────────────────────┘
```

各层只能向下依赖，跨层访问一律走该层的公共 `__init__` API。
详见 [docs/architecture.md](docs/architecture.md)。

## 快速开始

### Docker（无需任何 API key）

`sparse-dev` profile 完全离线运行——不调用 LLM，也不调用 embedding 服务。

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
docker compose up -d --build
docker compose exec -T memory dmsa-memory-smoke
```

记忆 API 监听 `127.0.0.1:8000`，只读记忆浏览器监听 `127.0.0.1:8765`，
数据保存在 `memory-data` 命名卷。

```bash
export DMSA_MEMORY_API_KEY=dev-secret

curl -sS http://127.0.0.1:8000/health

curl -sS http://127.0.0.1:8000/add \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"demo-1","user_id":"demo-repo","session_id":"agent-a",
       "messages":[{"role":"user","content":"load_cache returns stale values under concurrency"},
                   {"role":"assistant","content":"Check the version before replacing the cache."},
                   {"role":"tool","content":"pytest tests/test_cache.py: 3 passed"}]}'

curl -sS http://127.0.0.1:8000/search \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"load_cache stale value","user_id":"demo-repo","top_k":5,"options":[]}'
```

### 源码安装

需要 Python 3.10+ 与支持 FTS5 的 SQLite。固定版本的 PMD 源码归档随仓库提供，
不依赖网络上无法核验的同名包。

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install ./vendor/pmd-0.1.0.tar.gz
pip install -e ".[http,dev]" httpx

export DMSA_MEMORY_API_KEY=dev-secret
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

完整流程见 [docs/quickstart.md](docs/quickstart.md)。

## 从 Agent 侧接入

HTTP 服务是主要接入面：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 存活检查，免认证 |
| `POST` | `/add` | 持久化一次请求的有序消息（幂等） |
| `POST` | `/search` | 按 scope 检索证据 |
| `POST` | `/feedback` | 按 `trace_id` 记录检索反馈 |
| `POST` | `/task_result` | 上报任务结果事实（不触发进化） |
| `GET` | `/status` | embedding、向量索引与 RSI 是否真的生效 |
| `GET` | `/metrics` | 本进程延迟、交付条数与状态码计数 |
| `POST` | `/v1/memories/rsi/*` | 提案、进化与回滚检索策略 |

MCP server 通过 stdio 把同样的能力暴露为 14 个 `dmsa_coding_memory_*` 工具，
也可以转发到远端 HTTP 后端。接入说明见 [integrations/](integrations/) 与
[docs/integrations.md](docs/integrations.md)。

> **检索到的证据是不可信输入。** 历史记录可能过期、有误，甚至携带提示注入。
> 每一条取回的切片都只是数据，必须与当前代码和测试相互验证——绝不能当作要执行的指令。

## 离线 RSI

检索策略可以变，但必须过门禁：

1. 开发集上的失败被归因为一个明确的类型化诊断。
2. 语义梯度聚合把同类失败聚类，提出一个小而声明式的候选策略。
3. baseline 与 candidate 在冻结语料上做配对回放。
4. 门禁拒绝 NaN 指标、缺测、超预算，以及保护任务上任何已观察到的退步。
5. 被接受的候选成为可激活的版本，并可随时回滚。

advisor 只能在规则生成的候选里做选择，不能改写策略代码。
详见 [docs/algorithms/offline-rsi.md](docs/algorithms/offline-rsi.md)。

## 评测

`benchmarks/coding/` 提供可复现的评测框架：合成代理 fixture、多臂对照、消融，
以及自出题自解题闭环。仓库内 fixture 是合成的——它用于发现管线与检索的回归，
不能替代正式评测。方法论见 [docs/evaluation-runbook.md](docs/evaluation-runbook.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | 从 clone 到取回第一条记忆的五分钟路径 |
| [docs/coding-memory-guide.md](docs/coding-memory-guide.md) | 完整技术指南：启动、HTTP 契约、Agent 接入、配对实验 |
| [docs/design/coding-memory.md](docs/design/coding-memory.md) | 设计取舍：记忆表示、Add/Search、RSI 约束 |
| [docs/api-reference.md](docs/api-reference.md) | 公共 API |
| [docs/algorithms/](docs/algorithms/) | 检索、扩展能力与离线 RSI 算法 |
| [docs/deployment.md](docs/deployment.md) | Docker、密钥、生产拓扑与独立分发 |
| [docs/security-and-data.md](docs/security-and-data.md) | 数据边界、保留策略与信任模型 |
| [docs/USAGE.md](docs/USAGE.md) | 长篇用户手册 |
| [docs/paper-mapping.md](docs/paper-mapping.md) | 算法与文献的对应关系 |

同样的内容由 `Docs` 工作流构建成文档站，每次推送到 `main` 都会以
`mkdocs build --strict` 重新构建。

## 开发

```bash
make install      # 项目 + 固定版本 PMD 归档
make lint         # ruff check + format --check
make type         # mypy --strict
make cov          # 带 80% 覆盖率门禁的 pytest
make ci           # 以上全部
```

CI 在 Linux、macOS、Windows 与 Python 3.10–3.12 上跑 ruff、strict mypy 和 pytest，
外加覆盖率门禁、依赖审计、wheel/sdist 构建与独立容器契约测试。

- [CONTRIBUTING.md](CONTRIBUTING.md) —— 提交流程与代码规范
- [AGENTS.md](AGENTS.md) —— 在本仓库工作的 AI 编码 Agent 规范
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) —— 行为准则
- [SECURITY.md](SECURITY.md) —— 安全漏洞报告方式
- [CHANGELOG.md](CHANGELOG.md) —— 变更记录

## 状态与边界

当前是 `0.2.0.dev0` 开发版。明确说明：

- 稠密通道是**有界的作用域内扫描**（默认最多 10,000 个切片），不是大规模 ANN 检索。
- HTTP 接口只支持文本，`options` 只接受空数组。`/v1/runs` 与新版 MCP 桥未实现。
- 单一 API key 授权整个服务。`user_id` 是检索分区，**不是**用户级认证。
- 稠密模式会把源文本发给配置的 embedding 服务商；`sparse-dev` profile 不向外发送任何数据。
- 服务商兼容性与生产规模下的评测表现尚未验证。
- 原有 PMD DAG、SGA 文本梯度与 ToolBP 仍然可用，但尚未与 Coding Memory 引擎自动共享记忆。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE) 与
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。随附的 PMD 源码快照归属
PMD Maintainers，出处与完整性记录见 [vendor/README.md](vendor/README.md)。
