## 0.2.0.dev0 (development)

- Added the optional Coding Memory HTTP API and CLI.
- Added explicit sparse development and configured text-embedding-v4 profiles.
- This is an unpublished development build; scale and provider compatibility remain unvalidated.

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 初始项目脚手架（PD1-15 全套）
- L1 记忆层（封装 PMD skeleton）
- L2 SGA 文本梯度聚合（HDBSCAN 兼容 + 零依赖 fallback）
- L3 Tool-Use 反向传播（JSON Schema AST 投影）
- L4 MCP server（7 个工具暴露给 Agent）
- L5 三个 Integration：Claude Code、OpenCode、Hermes skill
- **`dmsa-inspect` CLI 只读诊断工具**（5 个子命令：dag / node / lineage / metrics / versions）
- 6 个可运行示例
- 完整 CI/CD（ruff + mypy strict + pytest + 多平台）
- 完整文档（README + **USAGE 用户文档含使用过程检测** + architecture + api-reference + integrations + paper-mapping）
- **真实 LLM 后端**（`dmsa.gradient.llm`，可选依赖 `pip install hermetic-dmsa[llm]`）：`OpenAIEmbedder`（真实语义嵌入，支持 OpenAI 兼容 endpoint）+ `LLMFusion`（LLM 改写 fused_text，可配 `fallback` 降级到 StaticFusion）+ `build_embedder` 工厂；`GradientConfig` 新增 `fusion_backend` / `llm_chat_model` / `llm_base_url` / `llm_api_key_env`（env：`DMSA_GRADIENT__FUSION_BACKEND=llm` 一键切换）
- `MemoryLayer.attach_embedder` / `MemoryLayer.apply_text_gradient` 公共 API；`dmsa_feedback` handler 支持 `set_aggregator` 注入自定义聚合器
- **`benchmarks/` 实验框架**（需 LLM endpoint，env：`DMSA_BENCH_API_KEY` / `DMSA_BENCH_BASE_URL`）：`bench_sga_conflict.py`（SGA vs TextGrad 式拼接基线，冲突反馈下的震荡/收敛对比，对应论文 §5）；`bench_toolbp.py`（ToolBP 改 schema vs prompt-only 堆错误，工具格式错误率对比，对应论文 §6）；结果落盘 `benchmarks/results/*.json`

### Fixed
- PMD skeleton `pyproject.toml` 的 `[project.optional-dependencies.dev]` 嵌套语法改为 list（解决新版 setuptools 报错 `dev must be array`）
- hermetic-dmsa 的 setuptools_scm `version_scheme=post` 改为静态 version（兼容新版 setuptools_scm 8+）
- PMD skeleton 路径依赖格式更新为不写 `@ file=...`，避免新版 pip 报 `Invalid URL`
- **MCP server 在 Windows 上无法通过 stdio 运行**：`run_stdio` 改用后台线程 + `asyncio.Queue` 读 stdin（ProactorEventLoop 不支持 `connect_read_pipe` 接管 stdin 管道），新增 `tests/test_stdio_integration.py` 真实子进程集成测试覆盖
- **`dmsa_search` 在默认 MCP server 下报错**（`requires an embedder`）：`MCPServer` 构造时自动为零 embedder 的 layer 挂 `HashEmbedder`（新增 `MemoryLayer.attach_embedder`），CLI 的 `--embedder-dim` 参数同步接线
- **`dmsa_apply_gradient` 假实现**：现在真正把融合后的文本梯度写回 MemoryLayer（新增 `MemoryLayer.apply_text_gradient`，生成带 lineage 的 successor 节点，可 snapshot/rollback；`requires_grad=False` 节点明确拒绝）；`dmsa_feedback` 的聚合结果按 feedback_id 缓存供 apply 查询；schema 目标暂明确报错并指向 `ToolBPEngine`

[Unreleased]: https://github.com/anthropic-research/hermetic-dmsa/compare/main...HEAD
# 0.2.0.dev0 (development)

- Added optional authenticated Coding Memory HTTP API and `dmsa-memory-http`.
- Added explicit sparse development and text-embedding-v4 competition profiles.
- This is an unpublished development version; no deployment or scale validation is claimed.
