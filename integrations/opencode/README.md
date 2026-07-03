# OpenCode Integration

> 30 分钟接入 hermetic-dmsa 到 OpenCode。

## 步骤

### 1. 安装 hermetic-dmsa

```bash
cd hermetic-dmsa
pip install -e ../analysis/pmd_skeleton
pip install -e .
```

### 2. 配置 `opencode.json`（项目根 或 `~/.config/opencode/opencode.json`）

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

### 3. 重启 OpenCode

```bash
# 重新启动 OpenCode 进程
```

### 4. 验证

在 OpenCode 中运行：

> 列出所有 MCP 工具

应该看到 7 个 `dmsa_*` 工具。

## 故障排查

| 问题 | 排查 |
|---|---|
| MCP server 启动失败 | 检查 `~/.hermetic-dmsa/` 目录可写；运行 `dmsa-mcp-server --verbose` 手动跑 |
| 工具未注册 | 检查 `opencode.json` 语法；重启 OpenCode |
| 中文文件名乱码 | PowerShell GBK 编码问题；用 IDE 编辑器直接写 UTF-8 |

## 注意事项

OpenCode 的 MCP 配置与 Claude Code 略有不同 — 它使用 `mcp` 顶层 key 而非 `mcpServers`。
