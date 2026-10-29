# 独立部署与开源分发

安全边界和数据保留见 [security and data](security-and-data.md)。比赛运行流程见
[competition runbook](competition-runbook.md)；合成 fixture 不代表官方成绩。

从本仓库 checkout 即可构建，不需要研究工作区或 sibling checkout。
需要 Docker Engine + Compose v2，或者 Python 3.10+ 与支持 FTS5 的 SQLite。
本文命令在仓库根目录执行。

## 无付费凭据启动

```bash
docker compose up -d --build
docker compose ps
docker compose exec -T memory dmsa-memory-smoke
```

默认 sparse-dev 不调用 LLM / embedding。命名卷 `memory-data` 保存
`/data/coding.sqlite3`、WAL 与共享内存文件。记忆 API 仅发布到
`127.0.0.1:8000`；`memory-browser` 以只读方式挂载同一卷并发布到
`127.0.0.1:8765`。
镜像以 UID/GID 10001 运行，根文件系统只读，移除 Linux capabilities，并使用
no-new-privileges；仅 `/data` 和临时 `/tmp` 可写。无需在镜像中放凭据。

复制 `.env.example` 到 `.env`，并设置随机的 `DMSA_MEMORY_API_KEY`。
如需发布到其他接口，可设置 DMSA_HTTP_BIND；公网部署应通过 TLS 反向代理接入，
并按业务信任边界限制调用方。API key 授权访问整个服务，user_id 不是用户认证。
`.env`、secrets/ 和本地数据库均不进入 Git 或 Docker 构建上下文。

`GET /health` 无需认证，只验证进程响应，不调用付费供应商，也不代表检索质量。
镜像每 10 秒执行健康探针。完整 smoke 验证健康、认证 Add 返回契约和 Search
检索到指定来源；成功退出 0，失败退出 1，仅记录固定消息，不输出 key 或原文。
反复运行默认 smoke 是幂等操作，会保留一条测试记录和独立测试 scope。

## 日志与故障定位

服务日志默认开启，走进程 stderr，因此 `docker logs` 直接可见，容器内不需要可写日志目录：

```bash
docker compose logs --since 5m memory
docker compose logs -f memory | grep -F 'request completed'
```

启动第一件事是打印一条生效配置摘要（不含任何密钥）：

```text
2026-10-29 10:06:19.765 | INFO     | dmsa.mcp_server.http.cli:_log_startup:149 | coding memory http listening url=http://127.0.0.1:8000 profile=sparse-dev storage=/data/coding.sqlite3 dimension=1024 embedding_model=disabled vector_index=sqlite rsi_enabled=true rsi_models=false log_level=INFO log_format=text
```

每条日志固定包含毫秒时间戳、级别、`logger:函数名:行号`、消息本体和结构化字段；
请求链路上的行还带 `trace=<id>`，与响应头 `X-Request-ID` 一致，可直接把某一次调用在
调用方与服务方两侧串起来。上游若自带 `X-Request-ID` / `traceparent`，服务沿用该值。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DMSA_LOG_LEVEL` | `INFO` | `TRACE`/`DEBUG`/`INFO`/`WARNING`/`ERROR`/`OFF`；`DEBUG` 追加成功请求与工具调用明细 |
| `DMSA_LOG_FORMAT` | `text` | `json` 供日志采集器解析，字段见 [usage](USAGE.md) |
| `DMSA_LOG_COLOR` | `auto` | 仅 TTY 上色；`docker logs` 非 TTY 因此保持纯文本 |
| `DMSA_LOG_FILE` | 空 | 本机排障可加滚动文件；只读根文件系统下留空 |

`compose.yaml` 与 `deploy/compose.competition.yaml` 已透传前三项。级别调成 `DEBUG`
只会多打明细行，不会改写数据。

日志与访问日志的分工：uvicorn 继续输出 `GET /health ...` 访问行；中间件只在
`WARNING`/`ERROR`（4xx/5xx）时补一条带 trace 与 `duration_ms` 的 `request completed`，
2xx 降为 `DEBUG` 避免与访问行重复。健康检查固定走 `DEBUG`，每 10 秒一次的探针不会
淹没日志。认证失败、请求体不合法、数据库故障与 RSI 失败各自有独立告警行，但只记录
状态码、字段数量与错误类型，不落请求原文，也不落 API key。

stdio 容器（`agent-mcp` profile）的 stdout 是 JSON-RPC 通道，日志同样只写 stderr，
协议帧不会被污染。

## 指标与状态出口

日志回答"某一次调用发生了什么"，指标回答"这段时间整体是什么样"。两者都从同一个进程出来，
容器里不需要额外的可写目录：

```bash
# 能力开关：嵌入/向量索引/RSI 是否真的生效（不含密钥与请求原文）
docker compose exec -T memory python -c "import os,httpx as h;print(h.get('http://127.0.0.1:8000/status',headers={'Authorization':'Bearer '+os.environ['DMSA_MEMORY_API_KEY']},timeout=10).json())"

# 进程内指标快照（重启清零）
docker compose exec -T memory python -c "import os,httpx as h;print(h.get('http://127.0.0.1:8000/metrics',headers={'Authorization':'Bearer '+os.environ['DMSA_MEMORY_API_KEY']},timeout=10).json())"
```

镜像里没有 curl，所以用容器自带的 python 与 httpx；密钥只从环境变量取，不落命令行。
服务端口绑在 `127.0.0.1` 时，也可以直接在宿主机上用 `.env` 里的 key 访问
`http://127.0.0.1:8000/status`。

`/status` 里最值得盯的三个字段：`embedding.enabled`（为 `false` 说明稠密通道整条都没接上）、
`vector_index`（`sqlite` 表示没走 Qdrant）、`rsi.active_policy`（回放门禁当前生效的那一份）。

`/metrics` 是 `{"counters": [...], "histograms": [...], "uptime_seconds": ...}`；直方图是
累积分桶，额外给出 `min`/`max`/`mean`/`p50`/`p95`/`p99`，没有观测过时为 `null`。排障时按
这条链路逐段切开延迟，就能判断时间花在哪儿：

| 指标 | 含义 |
| --- | --- |
| `dmsa.http.latency` / `dmsa.http.<route>.latency` | 整请求与分路由耗时（健康检查不计入） |
| `dmsa.http.requests.<2xx\|4xx\|5xx>` | 状态码族计数；`dmsa.http.health_checks` 单独计数 |
| `dmsa.search.latency` / `dmsa.search.returned` | 检索主流程本体耗时与实际交付条数（对齐固定 `top_k` 契约） |
| `dmsa.embedding.query.latency` / `.batch.latency` | 上游 embedding 调用耗时；配 `.calls` / `.failures` |
| `dmsa.vector_index.latency` / `.returned` | Qdrant 调用耗时与返回 ID 数 |
| `dmsa.span.<name>.latency` | `start_span` 埋点的内部段落耗时（聚合、投影、血缘等） |

`dmsa.http.search.latency` 减去 `dmsa.search.latency` 就是认证、契约校验与装包序列化的开销；
`dmsa.embedding.query.latency` 的 `p95` 直接决定检索 `top_k` 能不能按预算交满。分位数落在
`+Inf` 桶时导出为 `null`，意思是超出量程，不要当成"很快"。

## 重启与数据恢复验收

```bash
docker compose exec -T memory dmsa-memory-smoke --request-id durable-check
docker compose restart memory
docker compose up -d --wait
docker compose exec -T memory dmsa-memory-smoke --request-id durable-check --search-only
docker compose up -d --force-recreate --wait
docker compose exec -T memory dmsa-memory-smoke --request-id durable-check --search-only
```

重启后 `--search-only` 不补写数据，必须能查到相同 request 的来源。
`docker compose down` 保留卷；`down --volumes` 会删除存储，不用于常规停机。
备份时先停止写入/停止服务，再备份整个卷；不要只复制活动 SQLite 主文件而漏掉 WAL。
恢复时保留目录 UID/GID 10001，并使用相同 profile 与向量维度。

## Competition 模式

选择明确的 Compose overlay：

```bash
docker compose -f compose.yaml -f deploy/compose.competition.yaml up -d --build --wait
docker compose exec -T memory dmsa-memory-smoke
```

启动前必须通过环境或 `.env` 显式设置：

| 配置 | 要求 |
|---|---|
| DMSA_MEMORY_API_KEY | 非空服务密钥，无开发默认值 |
| DMSA_EMBEDDING_BASE_URL | 显式 HTTPS embedding endpoint，不能含凭据、查询或 fragment |
| DMSA_EMBEDDING_API_KEY | 非空 embedding 供应商密钥 |
| DMSA_EMBEDDING_MODEL | `qwen3.7-text-embedding-flash` 或 `text-embedding-v4` |
| DMSA_EMBEDDING_DIMENSION | Qwen 默认 1024，且须匹配模型与 Qdrant collection |

缺项会拒绝启动，不创建数据库、不监听端口，也不回退至 sparse。
`.env.example` 提供用户指定的 Qwen DashScope 原生端点；写入按最多 20 条分批，
并把检索文本与底库文本分别标注为 `query` / `document`。Competition 使用独立
`competition-data` 卷，避免与 sparse 语料混用；运行时
供应商错误不会降级为哈希向量。健康检查不验证 key 是否被供应商接受；完整 smoke
会真实调用 embedding，可能产生费用。未声称比赛成绩。
Competition overlay 也启动只处于 Compose 内部网络的 Qdrant，持久卷为
`qdrant-data`，不发布 Qdrant host port。`DMSA_QDRANT_API_KEY` 可配置受保护的外部
Qdrant。Qwen embedding 请求超时为 60 秒且不会自动重试付费调用；OpenAI-compatible
v4 使用 SDK 重试。Qdrant 幂等写入/检索/删除
采用有界指数退避。外部故障会显式失败，不降级为 sparse。

正式部署可使用 Compose secrets。创建 Git 忽略的 `secrets/memory_api_key` 和
`secrets/embedding_api_key` 两个 UTF-8 文件，各放一个真实 key，再启动：

```bash
docker compose -f compose.yaml -f deploy/compose.competition.yaml -f deploy/compose.secrets.yaml up -d --build --wait
```

Compose secret 路径相对于首个 Compose 文件。确保非 root UID 10001 可读挂载文件；
本地 Compose secret 的宿主文件应限制访问。CLI 也直接支持 `DMSA_MEMORY_API_KEY_FILE`
和 `DMSA_EMBEDDING_API_KEY_FILE`。同一个 key 同时指定非空值和文件会报错，文件
缺失、无法读取或为空也会报错；错误不包含文件内容。不要把 key 放进命令行或 URL。

## 本地源码安装与分发

```bash
python -m venv .venv
# 激活 .venv 后执行；Windows: .venv\Scripts\Activate.ps1
python -m pip install ./vendor/pmd-0.1.0.tar.gz
python -m pip install -e '.[http,dev]' httpx
```

固定 PMD 源码包、许可证、来源和升级方式见 [vendor/README.md](../vendor/README.md)。
本项目保留独立的 PMD 依赖，没有在 dmsa 内维护另一份算法实现。完整源码发行包
含 vendor、Docker 与部署配置；标准 DMSA wheel 仍需独立安装配套 PMD wheel。
不要从公共索引随意安装同名 pmd。自动 tag release 构建源码包、DMSA wheel 和
依赖 wheelhouse 并附到 GitHub Release；在 PMD 有正式可验证的公共发行前不自动
单独向 PyPI 发布一个无法独立解析依赖的 DMSA wheel。

镜像构建先校验 PMD SHA-256，再按 `deploy/requirements.lock` 的版本及 hash
构建依赖 wheel，最后从 wheelhouse 离线安装到运行镜像。构建下载阶段需要网络，
运行 sparse 服务不需要网络。基础镜像采用固定 digest 的 Python 3.12 slim，
升级时审核新的安全更新 digest 并修改 Dockerfile；也可通过构建参数
`--build-arg PYTHON_IMAGE=<审核后的镜像引用>` 显式替换。

更新依赖锁：

```bash
uv pip compile pyproject.toml --extra http --extra vector --python-version 3.12 --universal --generate-hashes --find-links vendor --output-file deploy/requirements.lock
```

## 验证命令

```bash
python -m pytest tests/ -q
python -m pytest tests/container_contract.py -q --no-cov
python -m build
```

容器验收独立执行，创建随机 Compose 项目和临时命名卷；检查真实健康、Add/Search、
认证失败、容器重建后的持久化、non-root/只读/许可证元数据、competition 缺配置退出。
结束后只删除该随机测试项目的容器与测试卷。正常测试不要求 Docker；CI 的独立
container job 执行这组验收。
