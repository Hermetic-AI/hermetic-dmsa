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

### Fixed
- PMD skeleton `pyproject.toml` 的 `[project.optional-dependencies.dev]` 嵌套语法改为 list（解决新版 setuptools 报错 `dev must be array`）
- hermetic-dmsa 的 setuptools_scm `version_scheme=post` 改为静态 version（兼容新版 setuptools_scm 8+）
- PMD skeleton 路径依赖格式更新为不写 `@ file=...`，避免新版 pip 报 `Invalid URL`

[Unreleased]: https://github.com/anthropic-research/hermetic-dmsa/compare/main...HEAD
