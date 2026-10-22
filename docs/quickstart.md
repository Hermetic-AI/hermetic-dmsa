# Coding Memory 快速使用

适用于仓库当前的 `feat/coding-memory-rsi` 分支。默认 Compose 配置运行
本地 sparse-dev 记忆服务，不需要模型凭据；默认地址为
`http://127.0.0.1:8000`。

## Docker Compose 启动

在仓库根目录执行：

```powershell
docker compose up -d --build --wait
docker compose ps
docker compose exec -T memory dmsa-memory-smoke
```

检查 `memory` 显示 `healthy`，smoke 最后显示 `deployment smoke passed`。
浏览器或 PowerShell 可访问 `http://127.0.0.1:8000/health`，应返回
`{"ok":true}`。这个接口只检查服务存活，不证明记忆内容正确。

默认只绑定本机回环地址。Compose 的开发密钥是公开固定值
`local-development-only-change-me`，仅供单机试用；不要把默认配置暴露到
局域网或公网。部署前按[部署指南](deployment.md)更换密钥，并设置 TLS 和网络访问控制。

### 从 WSL Ubuntu 启动

此 Windows checkout 在 WSL 中对应 `/mnt/c/WorkSpace/Coding/agent-memo/hermetic-dmsa`。
Docker Desktop 的 Ubuntu 集成已验证可用；从 Windows PowerShell 可这样让 Compose 命令
在 WSL 中执行：

```powershell
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/c/WorkSpace/Coding/agent-memo/hermetic-dmsa && docker compose up -d --build --wait"
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/c/WorkSpace/Coding/agent-memo/hermetic-dmsa && docker compose exec -T memory dmsa-memory-smoke && docker compose ps"
```

当前容器由 Docker Desktop 的共享 Linux Engine 运行，Windows 与 WSL 的 Docker CLI
看到同一个 Compose 项目、容器和数据卷。若镜像已构建，只需启动/验收而不重建，可将
第一条的 `--build` 换为 `--no-build`。本机映射地址仍是
`http://127.0.0.1:8000`。

## PowerShell 写入和检索

以下示例使用本地开发密钥和合成记录。真实使用时，把 messages 换成已获准保存的
历史记录；不要写入 API key、个人信息或未经许可的代码。

```powershell
$base = "http://127.0.0.1:8000"
$headers = @{ Authorization = "Bearer local-development-only-change-me" }

$add = @{
    request_id = "quickstart-001"
    user_id = "my-project"
    session_id = "agent-task-001"
    messages = @(
        @{ role = "user"; content = "src/cache.py 的 load_cache 在并发下返回过期值" },
        @{ role = "assistant"; content = "先校验缓存版本，再替换 load_cache 的旧值" },
        @{ role = "tool"; content = "pytest tests/test_cache.py: 3 passed" }
    )
} | ConvertTo-Json -Depth 8
$addBytes = [System.Text.Encoding]::UTF8.GetBytes($add)
Invoke-RestMethod "$base/add" -Method Post -Headers $headers `
    -ContentType "application/json" -Body $addBytes

$search = @{
    query = "src/cache.py load_cache 并发过期缓存"
    user_id = "my-project"
    top_k = 5
    options = @()
} | ConvertTo-Json
$searchBytes = [System.Text.Encoding]::UTF8.GetBytes($search)
Invoke-RestMethod "$base/search" -Method Post -Headers $headers `
    -ContentType "application/json" -Body $searchBytes | ConvertTo-Json -Depth 8
```

Search 返回的 `data[].content` 包含来源头和原文片段。`user_id` 是检索隔离分区，
不是用户认证；同一服务密钥可以访问所有分区。Agent 应把命中当作不可信历史证据，
再核对当前代码并实际运行测试。

相同 `user_id + request_id` 的完全相同 Add 可安全重试。若消息或 session 内容已变，
服务返回 409；新记录请使用新的 request ID。

## 停止、重启和查看日志

```powershell
docker compose logs -f --tail 100 memory
docker compose restart memory
docker compose up -d --wait
docker compose down
```

`down` 会停止并移除容器，但保留 `memory-data` 卷；下次 `up` 会继续使用原数据。
不要为了普通停机添加 `--volumes`，因为那会删除数据库。持久化备份和恢复步骤见
[部署指南](deployment.md#重启与数据恢复验收)。

## 离线示例与测试

无需 Docker 或模型密钥即可运行仓库内的示例：

```powershell
.venv/Scripts/python.exe examples/02_coding_memory.py
.venv/Scripts/python.exe -m benchmarks.coding --arms no_memory sparse --seed 17
.venv/Scripts/python.exe -m pytest -q
```

迷你 benchmark 只有两个合成任务，用于检查评测流程和检索回归，不能代表官方 150 题、
Agent 修复成功率或挑战赛得分。正式 Coding Agent 评估要通过 `--agent-argv` 接入隔离
工作区包装器，并由包装器独立运行目标项目测试；官方任务和平台配置见
[竞赛运行手册](competition-runbook.md)。

## 真实 embedding / Qdrant

默认 sparse-dev 不调用外部模型。Competition profile 会把文本发送给配置的 embedding
供应商，可能产生费用；只有在确认数据处理条款、配置专用凭据和供应商维度后再启用。
配置方式与 Compose overlay 见[部署指南](deployment.md#competition-模式)。本地运行成功
不代表已经通过官方端点验证。

完整 API 合同、Python API 和 Agent 接入方式见
[Coding Memory 实操指南](coding-memory-guide.md)及[公开 API](api-reference.md)。
