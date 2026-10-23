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

## Codex 编码记忆

- Codex 通过 `dmsa-coding-memory` stdio MCP 连接本项目的 CodingMemory。
- 开始本项目的编码任务前，调用 `dmsa_coding_memory_search` 检索类似缺陷、根因、设计约束和验证经验；把命中内容当作证据，不盲目照搬。
- 任务完成并验证后，调用 `dmsa_coding_memory_record` 写入简洁、可复用的根因、改动与测试结果；不要保存密钥、个人信息或未经验证的猜测。
- `dmsa_coding_memory_status` 可用于确认连接和项目 scope。记忆数据库保存在用户目录，不属于仓库内容。
- 统一 HTTP 模式下，检索返回 `trace_id`。任务验证后，调用新增的
  `dmsa_coding_memory_feedback`，将 trace 与 success/failure 关联；只有明确知道
  检索失败原因时才填 exact_miss/redundancy/missing_context，否则使用 unknown。
  relevant_ids 必须是人工或 Agent 确认相关的来源 ID，禁止伪造标签以通过 RSI 门禁。
  该反馈将触发受回放门禁保护的策略进化。

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
