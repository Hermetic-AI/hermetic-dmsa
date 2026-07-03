# Contributing to hermetic-dmsa

感谢您对 hermetic-dmsa 的贡献！

## 提交流程

1. Fork 本仓库
2. 创建分支：`git checkout -b feature/your-feature-name`
3. 写代码 + 写测试（覆盖率 ≥ 80%）
4. 跑 `make ci` 确保全部通过
5. 提交：`git commit -m "feat: ..."`（使用 [Conventional Commits](https://www.conventionalcommits.org/)）
6. 推送 + 提 PR

## 代码规范

- 每个 .py 文件 ≤ 300 行（超过必须拆分子模块）
- 中文 docstring + 英文变量名
- `from __future__ import annotations` 必备
- `mypy --strict` 必须通过
- 公共 API 必须有 Google 风格 docstring
- 错误必须 raise `DMSAError` 子类（参考 `src/dmsa/errors.py`）

## 测试

- 每个新功能必须有单元测试
- 关键路径必须有属性测试（`hypothesis`）
- 公共 API 改动必须有 integration test
- 覆盖率门禁 80%

## PR 模板

提 PR 时请说明：
- 解决什么问题
- 改动范围
- 测试覆盖情况
- 文档更新情况

详见 [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)。
