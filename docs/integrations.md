# Integrations

> 本页介绍原有 PMD DAG 的 MCP 工具。新版比赛用 Coding Memory HTTP 接口尚未接入这些 MCP 工具；接 Claude Code / Codex / OpenCode 使用新版记忆，请看 [新版接入与双 Agent 实验](coding-memory-guide.md#4-如何接入其他-agent)。两套存储不共享，需使用不同数据库文件。

> 把 hermetic-dmsa 接入 Claude Code / OpenCode / Hermes Agent 的完整步骤。

## 总览

| 目标 | 接入机制 | 文档 | 工程量 |
|---|---|---|---|
| **Claude Code** | `.mcp.json` MCP server | [`integrations/claude-code/README.md`](../integrations/claude-code/README.md) | 5 分钟 |
| **OpenCode** | `opencode.json` MCP server | [`integrations/opencode/README.md`](../integrations/opencode/README.md) | 5 分钟 |
| **Hermes Agent** | `SKILL.md` + 调 MCP server | [`integrations/hermes/README.md`](../integrations/hermes/README.md) | 15 分钟 |

## 共同前置条件

```bash
cd hermetic-dmsa
pip install ./vendor/pmd-0.1.0.tar.gz     # 固定 PMD 源码包
pip install -e .                            # hermetic-dmsa 本体
```

确认 `dmsa-mcp-server` 在 PATH 中：

```bash
which dmsa-mcp-server
# 应输出：/path/to/hermetic-dmsa/.venv/bin/dmsa-mcp-server
```

## MCP server 启动参数

```bash
dmsa-mcp-server [options]
    --storage PATH          SQLite 路径（默认 :memory:）
    --embedder-dim N        哈希嵌入维度（默认 64）
    --verbose               启用 verbose 日志
```

或通过环境变量：

| 变量 | 默认 |
|---|---|
| `DMSA_STORAGE__PATH` | `:memory:` |
| `DMSA_EMBEDDER__DIM` | `256` |
| `DMSA_GRADIENT__CLUSTERING_BACKEND` | `fallback` |
| `DMSA_GRADIENT__CONFLICT_THRESHOLD` | `0.3` |

## 验证接入成功

集成后，在 Agent 对话中说"列出你可见的工具"，应该看到 7 个 `dmsa_*` 工具：

1. `dmsa_add_memory`
2. `dmsa_query_lineage`
3. `dmsa_feedback`
4. `dmsa_apply_gradient`
5. `dmsa_tool_call`
6. `dmsa_snapshot`
7. `dmsa_search`

## 故障排查

### 工具列表为空

- 检查 `.mcp.json` / `opencode.json` / `SKILL.md` 语法
- 重启 Agent 进程
- 手动跑 `dmsa-mcp-server --verbose` 看 stderr

### 工具调用失败

- 看 Agent 日志（通常包含 JSON-RPC error）
- 检查 `~/.hermetic-dmsa/` 目录权限
- 用 `examples/01_quickstart_mcp.py` 离线复现

### Windows 编码问题

`dmsa-mcp-server` 输出含中文时 PowerShell 可能显示乱码。
解决：用 `PYTHONIOENCODING=utf-8` 启动。
