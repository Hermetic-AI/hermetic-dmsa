# hermetic-dmsa

**面向 Coding Agent 的记忆系统：保存历史证据，检索可复用经验，在开发集上改进检索策略。**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

开发版：`0.2.0.dev0`，代码位于 [`feat/coding-memory-rsi`](https://github.com/Hermetic-AI/hermetic-dmsa/tree/feat/coding-memory-rsi)。
旧版冻结标签：`baseline/pre-coding-rsi-20261022`。

**完整使用文档：[Coding Memory 技术方案与实操指南](docs/coding-memory-guide.md)**。
包含比赛接口、Windows/Linux 启动、Agent 接入、双 Agent 实验、结果检查和 RSI 使用方法。
首次启动请看 [快速使用文档](docs/quickstart.md)，含 Compose、PowerShell Add/Search、停机与数据保留。

## 最新方案做什么

```text
历史对话 / 代码修改 / 错误 / 测试输出
                  ↓ Add
原文持久化 + 来源定位 + 路径/符号索引 + 可选向量
                  ↓ Search
精确检索 + 全文检索 + 可选向量 → 融合排序 → 去重、补上下文、限制长度
                  ↓
当前 Agent 获得可追溯的历史证据，再自行定位、修改和运行测试

开发集失败案例 → 提出小幅策略修改 → 新旧策略配对回放 → 通过门槛才接受
```

- **记得清楚**：SQLite 保存原始消息及切片，结果包含 request/session/message 与字符偏移。
- **找得准确**：精确路径/符号、FTS5 全文和可选真实 embedding，通过 RRF 融合排序。
- **控制噪声**：重复过滤、同会话限额、邻近片段和返回预算。声称“通过测试”只记为自述。
- **可验证的 RSI**：离线调整检索策略；任何开发案例召回率下降都会阻止升级。

本版比赛路线使用独立的 `CodingMemory` 引擎。原有 PMD Memory DAG、SGA 文本梯度、ToolBP 和 MCP 工具仍保留，**尚未与 Coding Memory HTTP 共享记忆或自动联动**。

## 先在本地跑起来

独立 checkout 即可启动，无需付费模型凭据：

```bash
docker compose up -d --build
docker compose exec -T memory dmsa-memory-smoke
```

默认仅监听本机，使用持久卷。生产密钥、competition 模式、挂载 secrets、重启验收与开源分发见[部署指南](docs/deployment.md)。
以下源码安装需要 Python 3.10+ 与支持 FTS5 的 SQLite；固定 PMD 源码包已随仓库提供。

Windows PowerShell：

```powershell
git switch feat/coding-memory-rsi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ./vendor/pmd-0.1.0.tar.gz
python -m pip install -e ".[http,dev]" httpx
$env:DMSA_MEMORY_API_KEY = "dev-secret"
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

Linux/macOS：

```bash
git switch feat/coding-memory-rsi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./vendor/pmd-0.1.0.tar.gz
python -m pip install -e '.[http,dev]' httpx
export DMSA_MEMORY_API_KEY=dev-secret
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

`sparse-dev` 无需模型密钥，不调用 LLM 或 embedding 服务。默认监听 `127.0.0.1:8000`。
在另一个终端验证存活：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

返回 `ok=True` 只代表服务存活。继续按指南中的 [Add/Search 实例](docs/coding-memory-guide.md#3-比赛对应的-http-接口)验证写入与检索。

## 比赛和 Agent 怎么接

| 需求 | 当前入口 | 使用方法 |
|---|---|---|
| AML Coding Memory 的 Add/Search | HTTP `/add`、`/search` | 平台/适配器提交历史、检索证据；回答与评分由平台执行 |
| 自己的 Coding Agent 复用历史 | 同一组 HTTP 接口 | 开始任务先 Search；完成后 Add 原始经过及测试输出 |
| Claude Code / Codex / OpenCode 用新版记忆 | HTTP 客户端 + Agent shell/tool | 按[通用客户端与提示词](docs/coding-memory-guide.md#4-如何接入其他-agent)调用；目前没有自动安装的新版 MCP 桥 |
| 使用原有 DAG、反馈与 ToolBP | `dmsa-mcp-server` 的 7 个工具 | 见[原有 MCP 路径](docs/coding-memory-guide.md#7-原有-mcp-能力如何使用) |

真实向量模式使用 `--profile competition`，支持 `qwen3.7-text-embedding-flash` 的 DashScope 原生端点及兼容入口 `text-embedding-v4`。离线 RSI advisor 可用独立 decision/text chat 模型，但模型仅能在规则候选内选择，策略仍需经过固定回放门禁。配置和 Compose 部署见[快速使用](docs/quickstart.md)。尚未使用真实密钥完成供应商端点联调或取得比赛成绩。

## 如何验证

```bash
python examples/02_coding_memory.py
python examples/03_offline_rsi.py
python -m pytest tests/ -q
```

前两个是无网络示例；使用临时或内存数据库，退出后不保留数据。完整测试生成 `htmlcov/index.html`。
2026-10-22 的代码验证结果：**290 项测试通过，覆盖率 85.71%**，另有 **7 项真实容器验收通过**；本轮修改涉及的代码通过 Ruff/mypy。仓库已有的全量 lint/type 问题尚未全部清理。示例验证工程流程，不代表 Coding Agent 修复成功率。

想观察记忆是否帮助了另一个 Agent，按[双 Agent 对照实验](docs/coding-memory-guide.md#5-自己用其他-agent-测试记忆效果)操作。

## 当前边界

- 尚未验证官方评测端点、完整 300 实例或约 18 亿 Token 规模。
- Dense 是有上限的 scope 内扫描，默认最多 10,000 个切片，不是大规模 ANN 检索。
- RSI 改进的是开发集检索策略，不修改代码/模型权重；注册表中的策略不会自动加载到 HTTP 服务。
- HTTP 仅支持文本，`options` 只接受空数组；`/v1/runs`、新版 MCP 桥和可视化管理台未实现。
- API key 授权访问整个服务，`user_id` 是检索分区而非独立用户认证。多 Agent 共用项目时，由可信调用端固定 scope。
- 源码安装先安装仓库随附的固定 PMD 包；旧 MemoryLayer 的持久化 SQLite 快照回滚会显式拒绝。

## 文档导航

- [快速使用：Docker Compose 与 Add/Search](docs/quickstart.md)
- [技术方案、运行、比赛接口与 Agent 实验](docs/coding-memory-guide.md)
- [公开 API](docs/api-reference.md)
- [独立部署、Docker、密钥与开源分发](docs/deployment.md)
- [Coding Memory 算法](docs/algorithms/coding-memory.md) · [离线 RSI](docs/algorithms/offline-rsi.md)
- [论文映射](docs/paper-mapping.md) · [原有 MCP 集成资料](docs/integrations.md)
- [开发规范](AGENTS.md) · [变更记录](CHANGELOG.md)

Apache 2.0，见 [LICENSE](LICENSE) 与 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
