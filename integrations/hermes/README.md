# Hermes Agent Integration

> 把 hermetic-dmsa 作为 Hermes skill 接入。

## 步骤

### 1. 安装

```bash
cd hermetic-dmsa
pip install ./vendor/pmd-0.1.0.tar.gz
pip install -e .
```

### 2. 复制 skill 到 Hermes skills 目录

```bash
mkdir -p ~/.hermes/skills/hermetic-dmsa
cp integrations/hermes/SKILL.md ~/.hermes/skills/hermetic-dmsa/
```

### 3. 启动 dmsa-mcp-server 作为后台进程

```bash
# 长期运行的 background 进程
dmsa-mcp-server --storage ~/.hermetic-dmsa/hermes.sqlite &
```

或者用 systemd / launchd 守护（生产环境推荐）。

### 4. Hermes 调用方式

Hermes 会通过 `SKILL.md` 中的描述自动识别何时调用 dmsa 工具。
底层 Hermes 通过 stdio MCP 协议与 `dmsa-mcp-server` 通信（具体取决于 Hermes 的 MCP 支持情况）。

如果你的 Hermes 版本不支持 MCP，可直接通过 shell 调用 `dmsa-mcp-server` 的子进程。
