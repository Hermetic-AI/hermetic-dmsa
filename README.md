# hermetic-dmsa

> **Hermetic Differentiated Memory Self-evolving Agent**
> 生产级别的可微记忆层，让 Claude Code / OpenCode / Hermes / 任何 MCP 兼容 Agent 拥有「自动反向传播 + SGA 文本梯度聚合 + Tool-Use 反向传播」能力。

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-2025--06--18-green.svg)](https://modelcontextprotocol.io/)

---

## 它是什么

`hermetic-dmsa` 是一个 **生产级别的 Python 库 + MCP server**，把论文方向 1
（"基于文本反向传播的 Agent 长短期记忆可微自进化"）工程化：

- **Provenance-aware Memory DAG** — 每条记忆带 `creator_prompt_id` + `creator_agent_id`，可追溯
- **可微 Memory Schema** — `requires_grad` + `grad_learning_rate`，哪些记忆可被反向传播一目了然
- **SGA 文本梯度聚合** — HDBSCAN 密度聚类 + 冲突检测 + 条件分支生成，解决"详尽 vs 简短"这类文本梯度冲突
- **Tool-Use 反向传播** — 把 JSON Schema 视为可微 Variable，反向传播到工具描述本身（不止于 prompt）
- **30 分钟接入** — 任何能跑 MCP 的 Agent 一行配置即可使用

论文对应关系见 [`docs/paper-mapping.md`](docs/paper-mapping.md)。

## 快速开始

### 1. 安装

```bash
cd hermetic-dmsa
pip install -e ../analysis/pmd_skeleton    # 先装 PMD skeleton（路径依赖）
pip install -e ".[all]"                     # 装 hermetic-dmsa 全功能
```

零 LLM 依赖就能跑（默认 StaticFusion + HashEmbedder）。

### 2. 起 MCP server

```bash
dmsa-mcp-server --storage ~/.hermetic-dmsa/db.sqlite
```

server 在 stdio 上跑 MCP JSON-RPC 2.0。详见 [`docs/integrations.md`](docs/integrations.md)。

### 3. 接入 Claude Code

在 `~/.claude.json` 或项目根 `.mcp.json`：

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

重启 Claude Code，Agent 即可调用 7 个 `dmsa_*` 工具。

### 4. 跑端到端 demo

```bash
python examples/01_quickstart_mcp.py
```

演示「写入记忆 → 工具调用失败 → 反馈反向传播 → SGA 聚合 → 修改工具 schema → 下一轮成功」完整循环。

## 论文对应

| 论文创新点 | hermetic-dmsa 模块 |
|---|---|
| Provenance-aware Memory DAG | `dmsa.memory.*` |
| 可微 Memory Schema（requires_grad） | `dmsa.memory.layer` + `dmsa.types` |
| SGA 文本梯度聚合 | `dmsa.gradient.*`（6 个文件）|
| Tool-Use 反向传播 | `dmsa.toolbp.*`（5 个文件）|
| Differentiated L/S Memory | 通过 PMD `requires_grad` 默认值 |

详见 [`docs/paper-mapping.md`](docs/paper-mapping.md)。

## 开发

```bash
make ci          # ruff + mypy + pytest 严格模式
make test        # 只跑测试
make docs        # 构建文档站
make release     # 触发 PyPI 发布（需要先 git tag v*.*.*）
```

## License

Apache 2.0. 详见 [LICENSE](LICENSE) 与 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
