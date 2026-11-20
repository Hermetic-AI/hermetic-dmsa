# 快速使用

五分钟跑通 Coding Memory：启动服务、写入一条历史、检索回证据。
命令在仓库根目录执行；Windows 与 Linux/macOS 的差异已分别标注。

## 1. 启动（推荐 Docker）

```bash
docker compose up -d --build
docker compose exec -T memory dmsa-memory-smoke
```

`sparse-dev` 默认配置不调用 LLM 与 embedding 服务，无需任何付费凭据。
记忆 API 监听 `127.0.0.1:8000`，只读记忆浏览器监听 `127.0.0.1:8765`。
数据保存在命名卷 `memory-data`（`/data/coding.sqlite3`），容器重建不丢。

停止与清理：

```bash
docker compose stop          # 停止，数据保留
docker compose down          # 删除容器，数据卷保留
docker compose down -v       # 连同数据卷一起删除
```

## 2. 启动（源码安装）

需要 Python 3.10+ 与支持 FTS5 的 SQLite；PMD 源码包已随仓库提供。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ./vendor/pmd-0.1.0.tar.gz
python -m pip install -e ".[http,dev]" httpx
$env:DMSA_MEMORY_API_KEY = "dev-secret"
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

Linux/macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./vendor/pmd-0.1.0.tar.gz
python -m pip install -e '.[http,dev]' httpx
export DMSA_MEMORY_API_KEY=dev-secret
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

## 3. 写入与检索

另开一个终端。Linux/macOS 先 `export DMSA_MEMORY_API_KEY=dev-secret`。

```bash
export DMSA_MEMORY_API_KEY=dev-secret
export BASE=http://127.0.0.1:8000

# 存活检查（免认证）
curl -sS "$BASE/health"

# 写入一条历史
curl -sS "$BASE/add" \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"demo-add-001","user_id":"demo-repo","session_id":"agent-a-task-001",
       "messages":[{"role":"user","content":"src/cache.py 的 load_cache 在并发请求下返回过期值"},
                   {"role":"assistant","content":"定位到 src/cache.py load_cache；先校验版本，再替换缓存。"},
                   {"role":"tool","content":"pytest tests/test_cache.py: 3 passed"}]}'

# 检索回证据
curl -sS "$BASE/search" \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"src/cache.py load_cache 过期缓存 修复","user_id":"demo-repo","top_k":5,"options":[]}'
```

检索结果里的 `content` 带来源引用头与原文片段。`user_id` 是记忆分区：
换一个 `user_id` 再检索，应看不到上面写入的记录。

写入是幂等的，幂等键为 `(user_id, request_id)`。原样重试同一请求不会重复写入；
改了内容再提交同一个 `request_id` 会返回 `409`。

## 4. 记忆浏览器

只读界面，浏览 SQLite 源记录、切片、经验卡与向量元数据（不显示向量数值）：

```bash
docker compose up -d memory-browser
```

浏览器只绑定 `127.0.0.1`，不提供写入或删除操作。按时间或 scope 翻页由 SQL 直接完成，
记忆库变大后仍可逐页浏览；全量导出等需要读取原始行的功能受扫描上限保护，命中过多时
页面会提示先选择单个 scope 或缩小时间范围。

## 5. 启用真实向量（可选）

稀疏模式已经可用。要启用 embedding 与向量检索，复制 `.env.example` 为 `.env` 并填写
供应商配置：

```bash
DMSA_EMBEDDING_BASE_URL=<你的 embedding 端点>
DMSA_EMBEDDING_API_KEY=<你的 key>
DMSA_EMBEDDING_MODEL=qwen3.7-text-embedding-flash
DMSA_EMBEDDING_DIMENSION=1024
```

然后以 `--profile competition` 启动。启动后先读 `GET /status`，确认
`embedding.enabled=true`；`false` 时检索只剩稀疏与精确通道，命中量会明显偏低。
向量后端、密钥挂载与生产部署见[部署指南](deployment.md)。

## 下一步

- 完整技术方案、HTTP 契约、Agent 接入与双 Agent 对照实验：
  [技术方案与实操指南](coding-memory-guide.md)
- 公开 API：[api-reference.md](api-reference.md)
- 算法说明：[coding-memory.md](algorithms/coding-memory.md) ·
  [offline-rsi.md](algorithms/offline-rsi.md)
- 数据边界与安全：[security-and-data.md](security-and-data.md)
