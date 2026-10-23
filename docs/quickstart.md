# Coding Memory 快速使用

适用于仓库当前的 `feat/coding-memory-rsi` 分支。默认 Compose 配置运行本地 sparse-dev
记忆服务和只读浏览器，不需要模型凭据；地址分别为 `http://127.0.0.1:8000` 和
`http://127.0.0.1:8765`。

## 在 Codex 中使用编码记忆

仓库提供 `dmsa-coding-memory` stdio MCP 服务，直接访问 `CodingMemory` SQLite 存储，
不依赖 Docker HTTP 服务或外部模型。Codex 配置好并重启后，可使用以下工具：

- `dmsa_coding_memory_search`：编码前按任务、错误、文件或符号检索历史经验。
- `dmsa_coding_memory_record`：验证完成后显式记录根因、改动和测试结果。
- `dmsa_coding_memory_status`：查看当前数据库与项目隔离范围。

本仓库的 `AGENTS.md` 约定编码前检索、验证后记录。记忆文件默认位于
`~/.dmsa/coding-memory.sqlite`，可用 `DMSA_CODING_MEMORY_PATH` 更改；不同项目由
`DMSA_CODING_MEMORY_SCOPE` 隔离。

## 打开本地记忆浏览器

安装开发依赖后，在仓库目录运行：

```powershell
.venv/Scripts/python.exe -m dmsa.inspect.memory_browser
```

然后打开 `http://127.0.0.1:8765`。默认只读打开 Codex MCP 使用的
`~/.dmsa/coding-memory.sqlite`；也可用 `--database PATH --port 8765` 指定其他
DMSA SQLite 文件。页面支持全文筛选、scope、缺陷/功能类型、结果、角色、根因/验证/失败槽位、
向量状态/模型/维度、request/session、路径/符号、revision、消息序号、时间和原文区间筛选，
以及 JSON/CSV 导出。也可导入 DMSA JSON 或标准化 JSONL 快照。

DMSA 当前以 SQLite 保存原始来源、消息和记忆切片；Qdrant（若启用）只是派生 ANN 向量索引，
不会独立保存可还原的完整记忆正文。浏览器因此展示 SQLite 源记录和向量元数据，不显示向量数值；
浏览器只绑定 `127.0.0.1`，不提供写入或删除操作。

## 模型配置与 Docker Compose 启动

首次使用复制模板并编辑 `.env`。密钥只写入被 Git 忽略的 `.env`，不要提交或粘贴到
源码。模板已填好你给的端点和模型名：

```powershell
Copy-Item .env.example .env
# 编辑 .env：设置长随机 DMSA_MEMORY_API_KEY，并填入 embedding、decision 和 text API key
docker compose up -d --build --wait
docker compose ps
docker compose exec -T memory dmsa-memory-smoke
```

检查 `memory` 显示 `healthy`，smoke 最后显示 `deployment smoke passed`；浏览器打开
`http://127.0.0.1:8765` 查看 Compose volume 中的实际记忆。sparse-dev 不调用模型。

启用 Qwen 向量检索时，在 `.env` 设置 `DMSA_PROFILE=competition` 并填好 embedding key，
然后执行：

```powershell
docker compose -f compose.yaml -f deploy/compose.competition.yaml up -d --build --wait
```

这会启动 Qdrant，并让只读浏览器挂载同一 competition 数据卷。Qwen 原生 embedding
每批最多 20 条；默认维度 1024，支持设置为 `256/512/768/1024/1536/2048/2560`。
首次 Add/Search 会产生真实模型请求和相应费用；现有 sparse 记忆不会自动补向量。

`decision-model-preview` 与 MiniMax 配置在 `.env`。离线 RSI 让决策模型从规则生成的候选中
选择，并由 MiniMax 写候选说明；它不会自动修改运行策略。可用 JSON 通过一次性 Compose
任务请求建议：

```powershell
@'
{"current_policy":{},"diagnostics":[{"kind":"exact_miss","failures":2,"success_counterexamples":0}]}
'@ | docker compose --profile rsi run --rm -i rsi-advisor
```

模型建议之后仍必须按[离线 RSI 指南](algorithms/offline-rsi.md)运行 `run_replay` 并通过固定门禁。
MiniMax 也可经一次性 `--profile mcp` stdio 服务用于旧 SGA 融合：
`docker compose --profile mcp run --rm -i agent-mcp`。

所有服务默认只绑定本机回环地址。Qwen 会把待向量化的记忆文本和检索查询发送到你配置的
供应商端点；请先确认数据使用权限。部署到局域网或公网前按[部署指南](deployment.md)
设置 TLS 和访问控制。

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
第一条的 `--build` 换为 `--no-build`。本机映射地址是
`http://127.0.0.1:8000`，浏览器是 `http://127.0.0.1:8765`。

## PowerShell 写入和检索

以下示例使用本地开发密钥和合成记录。真实使用时，把 messages 换成已获准保存的
历史记录；不要写入 API key、个人信息或未经许可的代码。

```powershell
$base = "http://127.0.0.1:8000"
$headers = @{ Authorization = "Bearer <DMSA_MEMORY_API_KEY from .env>" }

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
