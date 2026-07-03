# Third-Party Licenses

本项目依赖下列第三方包（按字母序）。

## 强制依赖

| 包 | License | 用途 |
|---|---|---|
| pmd | Apache 2.0 | 底层记忆库（`analysis/pmd_skeleton`）|

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
