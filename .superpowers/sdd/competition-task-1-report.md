# Task 1 完成报告：独立打包与 Docker Compose 部署

日期：2026-10-22。分支：`feat/coding-memory-rsi`。
状态：`DONE_WITH_CONCERNS`；功能与验收完成，已有全仓 lint/type 债务仍保留。

## 设计选择

PMD 采用随仓库提供的、固定 SHA-256 的完整源码发行包，而不是运行时依赖
未记录的 sibling checkout，也不将 PMD 实现复制进 dmsa 命名空间维护第二个分叉。
`vendor/pmd-0.1.0.tar.gz` 来自研究工作区 `analysis/pmd_skeleton` 的 2026-10-22
快照。源目录没有独立 Git 历史，因此不编造上游 commit。归档中的 30 个 Python
库源文件已逐一与来源比较，字节相同；原 README/pyproject 声明 Apache 2.0，
补入完整 Apache-2.0 许可证文本，保留原作者元数据，并在 NOTICE 记录出处。

归档 SHA-256：
`909fa85cf4a1256d022587a6ec8a4196f50ac8d35e01f3cc19c32fb8c4550c6f`。
维护时先在权威 PMD 来源修改并验证，再显式重建归档、更新来源记录与校验值。
源码发行包包含 vendor；标准 DMSA wheel 保留独立 PMD 依赖。tag release 改为
GitHub 源码、wheel 与依赖 wheelhouse 分发，在 PMD 具备正式公共发行前不自动
单独发布无法解析该依赖的 PyPI 包。

Docker 使用固定 digest 的 Python 3.12 slim、多阶段构建、带版本和 hash 的运行
依赖锁、离线安装 wheelhouse。服务 UID/GID 10001、只读根文件系统、删除全部
capabilities、no-new-privileges；仅 `/data` 命名卷与 `/tmp` 可写。
默认 Compose sparse-dev 一条命令启动，无付费凭据，端口仅映射本机。
competition overlay 没有开发 key/model 默认值，使用独立命名卷并在配置不全时
启动前退出。独立 secrets overlay 演示 UTF-8 文件挂载与 `_FILE` 配置。

配置校验使用 `ConfigError`；CLI 输出固定字段名错误并隐藏异常链。
smoke 使用 `TransportError` 和固定日志，不输出密钥、上游错误正文或原始证据。
`--search-only` 不补写数据，必须找到相同 request 的已持久化证据。

## 文件变更

- 构建/部署：`Dockerfile`、`.dockerignore`、`compose.yaml`、`.env.example`、
  `deploy/compose.competition.yaml`、`deploy/compose.secrets.yaml`、
  `deploy/requirements.lock`。
- 依赖/分发：`vendor/pmd-0.1.0.tar.gz`、`vendor/SHA256SUMS`、`vendor/README.md`、
  `MANIFEST.in`、`pyproject.toml`、`.gitignore`、`Makefile`。
- 行为：`src/dmsa/mcp_server/http/runtime.py`、`smoke.py`、`cli.py`。
- 测试：`tests/test_deployment.py`、`tests/test_distribution.py`、
  `tests/container_contract.py`。
- CI：`.github/workflows/ci.yml`、`docs.yml`、`release.yml`；全部移除 sibling 安装，
  正常测试安装 HTTP 依赖，独立 container job 执行真实 Docker 验收。
- 文档/开源：`docs/deployment.md`、`README.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、
  `NOTICE`、`THIRD_PARTY_LICENSES.md`、`docs/api-reference.md`、
  `docs/coding-memory-guide.md`、`docs/coding-memory-dmsa-rsi-design.md`、
  `docs/USAGE.md`、`docs/integrations.md`，以及三个集成目录的 README 和 Hermes
  SKILL.md 中的安装命令。

原有未跟踪用户文件 `docs/superpowers/plans/2026-10-22-competition-completion.md`
没有改动、没有提交，也不会混入新的源码发行包。没有修改研究工作区 PMD 来源。

## TDD：Red / Green

1. 先增加部署测试，再运行：
   `python -m pytest tests/test_deployment.py -q --no-cov`。
   Red：17 failed。缺模型时原 CLI 实际到达 server 启动（测试阻止真实启动），
   其余失败来自缺失的 secret/runtime/smoke 功能。
   实现后 Green：17 passed。
2. 增加独立分发/工作流测试。Red：工作流仍引用 sibling checkout；另一个测试
   的源文件数初始误写为 32，核对归档后纠正为真实的 30。
   修改工作流后 Green：2 passed。
3. 自审增加 smoke 误判和坏 URL 回归测试。
   Red：2 failed, 20 passed（错误接受其他 probe 证据，坏 IPv6 URL 抛裸 ValueError）。
   修复后 Green：部署与分发合计 24 passed。
4. 容器测试独立执行，不在普通测试中跳过/伪装 Docker 检查。初版 4 passed；
   扩展后逐个移除四项 competition 必需配置，并检查 overlay 的独立卷与空默认值，
   最终 7 passed。

## 验证命令与输出

命令均在 hermetic-dmsa 根目录执行，Python 命令使用 `.venv/Scripts/python.exe`。

| 命令 / 检查 | 实际输出 / 结果 |
|---|---|
| `python -m pip install ./vendor/pmd-0.1.0.tar.gz --no-deps` | Successfully installed pmd-0.1.0 |
| `python -m pip install -e . --no-deps` | Successfully installed hermetic-dmsa-0.2.0.dev0 |
| `import pmd; pmd.__file__` | `.venv/Lib/site-packages/pmd/__init__.py`；不再导入 sibling |
| `python -m pytest tests/ -q --cov-report=term` | 290 passed in 16.05s；coverage 85.71%，高于 80% |
| `python -m pytest tests/test_distribution.py tests/test_deployment.py -q --no-cov` | 24 passed in 5.15s |
| `python -m pytest tests/container_contract.py -q --no-cov` | 7 passed in 47.57s |
| `ruff check` 对 CLI/runtime/smoke 和三个新增测试文件 | All checks passed! |
| `ruff format --check` 对相同六个文件 | 6 files already formatted |
| `mypy` 对 CLI/runtime/smoke `--strict` | Success: no issues found in 3 source files |
| `python -m build` | Successfully built hermetic_dmsa-0.2.0.dev0.tar.gz and hermetic_dmsa-0.2.0.dev0-py3-none-any.whl |
| 检查生成的源码归档 inventory | vendor、SHA256SUMS、Dockerfile、Compose overlays、锁、测试 conftest/container 文件、Makefile/AGENTS/CONTRIBUTING 均存在；用户计划不在包内 |
| 比较 PMD 归档全部 Python 文件与来源 | PMD source byte comparison: 30 files identical |
| `docker compose config --quiet` 及 competition overlay config | exit 0 |
| `git diff --check` / `git diff --cached --check` | exit 0 |

另行实际执行默认构建/启动与重启验收，使用隔离项目名避免影响用户现有服务：

```text
docker compose -p dmsa-task1-acceptance up -d --build --wait --wait-timeout 120
  Image hermetic-dmsa:local Built
  Container dmsa-task1-acceptance-memory-1 Healthy
docker compose -p dmsa-task1-acceptance exec -T memory dmsa-memory-smoke --request-id final-acceptance
  deployment smoke passed
docker compose -p dmsa-task1-acceptance restart memory
  Container ... Started
docker compose -p dmsa-task1-acceptance exec -T memory dmsa-memory-smoke --request-id final-acceptance --search-only
  deployment smoke passed
```

容器 suite 还实际执行 `--force-recreate --wait` 后只读 Search，确认命名卷恢复；
检查 UID/GID、只读根目录、许可证标签和 LICENSE/NOTICE 文件；错误认证必须失败；
四种缺配置的 competition 容器均非零退出，且未到达不可写数据库路径。
验收容器与专属临时卷已清理，未删除用户数据库或用户服务。

最初使用未缓存 bookworm tag 时遇到镜像层下载停滞；最终固定为已验证的 Python
3.12 slim digest 后，默认构建命令和完整容器测试通过。没有把网络故障当成通过。

## 提交

- 实现：`7a63aa3b3b7d5c68fc704c457c20d0a4ff5e6914`
  `feat: package standalone memory service with persistent Docker deployment`
- 本报告使用独立的后续文档提交；其 ID 可通过
  `git log -1 -- .superpowers/sdd/competition-task-1-report.md` 查询。
- 未 push。

## 自审

逐项核对 brief：独立 checkout、默认健康 sparse、Add/Search smoke、重启与重建
持久化、non-root、许可证/NOTICE、密钥示例、competition fail closed、普通/错误/
边界测试、部署与开源维护文档均有实现和实际验证。Add/Search 请求和返回格式没有
改变；没有改动 memory 算法、存储 schema 或层间方向。新增生产 Python 文件均
少于 300 行，中文 docstring、英文标识符、DMSAError 子类和统一 logger。

修复了自审发现的两个 probe 缺陷：旧 marker 误通过，以及坏 URL 的裸异常。
源码归档显式剔除本地计划文档。构建上下文为 allowlist，不含 `.env`、secrets/
或数据库，也排除了 Python cache。Competition 卷不会复用 sparse 卷。

## Concerns / 限制

1. 全仓静态门禁有既存债务，不能声称全量 Ruff/mypy 通过。
   当前 Ruff 为 1091 项；将任务开始时的 HEAD 归档到独立临时目录、在其根目录
   使用同一配置运行，仍为 1091 项。全仓 mypy 有原有 4 项：clustering.py 两项
   Optional iterable，dmsa/__init__.py 一项 unused ignore，serialization.py 一项
   no-any-return；baseline 同样复现。这些未扩大为本任务的全仓重构。
2. 官方 endpoint 与真实付费 key 尚未联调，未运行付费 embedding 或声称比赛成绩。
   `/health` 是存活检查；只有完整 smoke 才覆盖真实 Add/Search（competition 时会计费）。
3. 依赖源码分发方式已经独立，但 PMD 尚无可验证的公共独立发行/commit。
   本方案明确保留来源与固定 hash，并调整 release 为源码/wheelhouse 分发。
4. 构建时 setuptools 对已有 PEP 621 license 表和 classifier 发出弃用警告；
   构建成功。运行依赖有 hash 锁，隔离构建工具本身未做完整供应链锁定。
5. 默认服务 key 只面向 loopback 的本机 sparse 验证；生产需要替换密钥并设置合适
   的 TLS/调用方边界，相关步骤已文档化。没有改变服务级 key 与 user_id 的原有信任模型。

## 独立审查修复

独立审查发现 smoke CLI 对 `http.client.HTTPException` 的子类处理不完整，坏端口和
畸形状态行可产生 traceback 或暴露上游响应文本。将 Request 构造移入异常边界，并
把协议异常统一映射为固定消息的 `TransportError`。新增
`tests/test_smoke_errors.py` 覆盖坏端口、非法端口、畸形状态行、`BadStatusLine`、
`InvalidURL`、`IncompleteRead` 和 Request 构造错误；CLI 输出断言无 traceback、无上游
内容。修复后覆盖测试通过。

审查还发现 pip-audit 没覆盖部署锁文件。CI 现在审计 `deploy/requirements.lock` 中
所有公开运行依赖；只剔除本地 vendored 的 `pmd==0.1.0` 项，因为 PyPI 漏洞服务无法
解析项目附带的哈希固定源码发行包。PMD 的全部传递运行依赖仍在同一锁文件中，源码包
则由 SHA-256 校验。`tests/test_distribution.py` 检查这段 CI 合约。

修复验证：`pytest tests/test_smoke_errors.py tests/test_deployment.py tests/test_distribution.py
-q --no-cov` 为 33 passed；涉及文件 Ruff 与格式检查通过；smoke 模块 strict mypy
通过。`pip-audit` 使用未过滤锁文件时确实因本地 PMD 不在 PyPI 失败；CI 已按上述
方式过滤其唯一本地项目项。复审后全量审计结果与网络服务可用性需由 CI 输出确认。
