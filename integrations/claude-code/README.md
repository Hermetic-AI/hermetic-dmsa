# Claude Code Integration

> 30 分钟接入 hermetic-dmsa 到 Claude Code。

## 步骤

### 1. 安装 hermetic-dmsa

```bash
cd hermetic-dmsa
pip install -e ../analysis/pmd_skeleton
pip install -e .
```

确认 `dmsa-mcp-server` 在 PATH 中：

```bash
which dmsa-mcp-server
```

### 2. 创建 `.mcp.json`（项目根 或 `~/.claude.json`）

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

也可以用环境变量 `DMSA_STORAGE__PATH`：

```bash
export DMSA_STORAGE__PATH=~/.hermetic-dmsa/db.sqlite
```

### 3. 重启 Claude Code

```bash
# 重启 Claude Code 进程
```

### 4. 验证

在 Claude Code 对话中说：

> 列出你可见的 MCP 工具

应该看到 7 个 `dmsa_*` 工具：
- `dmsa_add_memory`
- `dmsa_query_lineage`
- `dmsa_feedback`
- `dmsa_apply_gradient`
- `dmsa_tool_call`
- `dmsa_snapshot`
- `dmsa_search`

### 5. 第一个端到端调用

让 Claude Code：

> 调用 `dmsa_add_memory` 写入一条 memory，content 是 "user prefers dark mode"，tags 是 ["user_pref"]。然后调用 `dmsa_query_lineage` 查询它的祖先。

Claude Code 会自动调用 MCP 工具并展示结果。

## 高级配置

### 自定义 embedder dim

```json
{
  "mcpServers": {
    "hermetic-dmsa": {
      "command": "dmsa-mcp-server",
      "args": ["--storage", "~/.hermetic-dmsa/db.sqlite", "--embedder-dim", "256"]
    }
  }
}
```

### 多 workspace 隔离

每个项目用不同 storage path：

```json
{
  "mcpServers": {
    "hermetic-dmsa-project-a": {
      "command": "dmsa-mcp-server",
      "args": ["--storage", "~/.hermetic-dmsa/project-a.sqlite"]
    }
  }
}
```

## 故障排查

| 问题 | 排查 |
|---|---|
| `dmsa-mcp-server` not found | `which dmsa-mcp-server`；检查 `pip install -e .` 是否成功 |
| 工具列表为空 | 重启 Claude Code；检查 `.mcp.json` 语法 |
| 工具调用失败 | 看 Claude Code 日志；运行 `dmsa-mcp-server --storage :memory: --verbose` 手动调试 |
