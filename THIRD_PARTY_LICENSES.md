# Third-Party Licenses

本项目依赖下列第三方包（按字母序）。

## 强制依赖

| 包 | License | 用途 |
|---|---|---|
| pmd 0.1.0 | Apache 2.0 | 底层记忆库；固定源码包及出处见 [vendor/README.md](vendor/README.md) |

PMD 源码完整随附于 `vendor/pmd-0.1.0.tar.gz`，包含 Apache-2.0 文本。
源码文件与原研究工作区快照保持字节一致，不在归档中维护独立分叉。
容器包含本项目 LICENSE / NOTICE / 本清单和 PMD 出处；安装的 wheel 还包含各自许可证。
锁定的 HTTP 运行依赖见 `deploy/requirements.lock`；FastAPI、Starlette、Uvicorn、
HTTPX/HTTPX2、AnyIO、Pydantic、NetworkX、ulid-py 均为 MIT/BSD 许可，
NumPy 为 BSD-3-Clause，OpenAI SDK 为 Apache-2.0；详细声明以发行包内许可证为准。

## 可选依赖

| 包 | License | 用途 | 何时启用 |
|---|---|---|---|
| openai | Apache 2.0 | LLM 客户端 | `pip install hermetic-dmsa[llm]` |
| hdbscan | BSD-3-Clause | 真实 HDBSCAN 聚类 | `pip install hermetic-dmsa[clustering]` |
| numpy | BSD-3-Clause | 向量运算 | `pip install hermetic-dmsa[clustering]` |
| mkdocs | BSD-2-Clause | 文档站 | `pip install hermetic-dmsa[docs]` |
| mkdocs-material | MIT | 文档主题 | `pip install hermetic-dmsa[docs]` |
| mkdocstrings | MIT | API 文档生成 | `pip install hermetic-dmsa[docs]` |

## Dev 依赖

| 包 | License | 用途 |
|---|---|---|
| pytest | MIT | 测试框架 |
| pytest-cov | MIT | 覆盖率 |
| ruff | MIT | linter + formatter |
| mypy | MIT | 类型检查 |
| pre-commit | MIT | git hooks |
| build | MIT | 构建分发 |
| pip-audit | Apache 2.0 | 漏洞扫描 |
| hypothesis | MPL-2.0 | 属性测试 |

## 标准库

本项目大量使用 Python 标准库（hashlib、json、dataclasses、sqlite3、asyncio、typing、pathlib、contextvars、threading、collections、functools、abc、enum、time、uuid、warnings、subprocess、select、struct、socket、tempfile、shutil、os、sys、math、re、itertools、statistics、bisect、heapq、array、string、textwrap、unittest、unittest.mock、dataclasses、typing、typing_extensions、logging、traceback、io、copy、pprint）。

详细 license 见 CPython 仓库 [LICENSE](https://github.com/python/cpython/blob/main/LICENSE)。
