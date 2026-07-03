# AGENTS.md — hermetic-dmsa 项目工作规范

## 项目定位

生产级别 Python 库 + MCP server + 多 Agent 集成包。**不是 demo**。每个 .py 文件 ≤ 300 行。

## 强制规范

- 中文 docstring + 英文变量名
- 严格 mypy + ruff
- 任何超过 300 行的 .py 必须拆分子模块
- 公共 API 改动必须有测试 + 更新 docs/api-reference.md
- 错误必须 raise DMSAError 子类，不允许裸 raise
- 禁止 `print()`，统一用 `dmsa.observability.logging`
- 禁止 `import *`
- 禁止从相对路径超过 3 层（`from ...dmsa.foo` 而非 `from ...dmsa.memory.layer import ...`）

## 分层与依赖规则

```
L0 pmd (外部)
L1 dmsa.memory      只能 import L0
L2 dmsa.gradient    只能 import L1, L0
L3 dmsa.toolbp      只能 import L2, L1, L0
L4 dmsa.mcp_server  只能 import L3, L2, L1, L0
L5 integrations/*   只能 import L4（通过 MCP 协议，不直接 import dmsa.*）
```

跨层访问必须经过该层的公共 API（`dmsa/memory/__init__.py` 导出的）。

## 测试

- 测试目录 `tests/`，文件名 `test_<module>.py`
- 每个公共 API 至少 3 个测试：正常路径 / 异常路径 / 边界条件
- 关键算法（`SGAAggregator` / `ToolBPEngine`）含属性测试
- 端到端测试在 `tests/test_end_to_end.py`
- 覆盖率门禁 80%（`pyproject.toml` 配置）

## 提交

- Conventional Commits：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
- 中文 commit message 也接受，但必须明确意图
- 重大重构前先开 issue 讨论

## 路径注意事项

- 本目录在 Windows 11 上构建，但必须保证 Linux/macOS 兼容
- 路径用 `pathlib.Path`，不要字符串拼接
- 锁：`threading.RLock`（线程）+ `fcntl` / `msvcrt`（进程）
- 进程间不共享状态（每个 MCP server 进程独立）

## 论文相关

- 任何算法改动同步更新 `docs/paper-mapping.md` + `docs/algorithms/*.md`
- 任何破坏性 API 改动先在 GitHub Discussions 讨论
- 论文相关代码优先：算法正确性 > 性能 > 美观
