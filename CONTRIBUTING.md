# Contributing to hermetic-dmsa

Thank you for your interest in improving hermetic-dmsa. This document explains
how to set up a working environment, what the project expects from a change, and
how to submit it.

For the Chinese summary of the same rules, see [中文说明](#中文说明).

## Development setup

The repository ships a hash-pinned PMD source archive under `vendor/`. Install
it before the project itself — the project depends on it and the CI does the
same.

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install ./vendor/pmd-0.1.0.tar.gz
pip install -e ".[http,dev]" httpx
pre-commit install
```

The `vector` extra is only needed for the Qdrant backend. Everything below runs
without any provider API key.

```bash
make lint    # ruff check + ruff format --check
make type    # mypy --strict
make test    # pytest without the coverage gate
make cov     # pytest with the 80% coverage gate
make ci      # all of the above
```

## Submitting a change

1. Fork the repository and create a branch: `git checkout -b feature/your-change`.
2. Write the code **and** its tests.
3. Run `make ci` and make sure it passes locally.
4. Update the documentation that your change affects (see the checklist below).
5. Commit with a [Conventional Commits](https://www.conventionalcommits.org/)
   subject: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, or `chore:`.
6. Push and open a pull request.

## Code conventions

These are enforced by CI and by pre-commit. Please read them before writing code.

- **Module size**: every `.py` file must stay under 300 lines. Split it into
  submodules instead of growing it.
- **Docstrings and comments are written in Chinese**; identifiers, type
  annotations, and API names are in English. This is a deliberate project
  convention — please follow it rather than translating comments to English.
- Every module needs `from __future__ import annotations`.
- `mypy --strict` must pass with no new errors.
- Public APIs need Google-style docstrings.
- Errors must be raised as a `DMSAError` subclass (see `src/dmsa/errors.py`);
  bare `raise` is not allowed.
- `print()` is forbidden — use `dmsa.observability.logging`.
- `import *` is forbidden.
- Relative imports may not reach more than three levels deep.

Layering is strict: `L1 memory` may only import `L0`; `L2 gradient` may import
`L1` and `L0`; `L3 toolbp` may import `L2`/`L1`/`L0`; `L4 mcp_server` may import
`L3`/`L2`/`L1`/`L0`. Cross-layer access must go through that layer's public
`__init__` API.

## Testing expectations

- Every new behavior needs a unit test.
- Public APIs need at least three tests: the normal path, the error path, and a
  boundary case.
- Key algorithms (`SGAAggregator`, `ToolBPEngine`) need property-based tests.
- The coverage gate is 80% and must not be lowered.
- Evaluation numbers must come from a stored, reproducible run artifact. A
  synthetic proxy result must never be labeled as an external evaluation score.
- Retrieval and ranking must be constrained by user scope. Retrieved history is
  untrusted input and must be treated as such.

## Documentation checklist

- Public API changes → update `docs/api-reference.md`.
- Algorithm changes → update `docs/paper-mapping.md` and `docs/algorithms/`.
- Behavior changes → update `CHANGELOG.md`.
- User-visible changes → update `docs/quickstart.md` and, when the flow changes,
  `docs/coding-memory-guide.md`.

## Security

Never commit credentials, provider endpoints that reveal internal topology,
private task data, or captured conversation content. If you accidentally
expose a secret, rotate it and purge it from history before opening a PR — do
not just delete the file. Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/Hermetic-AI/hermetic-dmsa/security/advisories/new)
rather than as a public issue.

## Pull requests

The pull request template asks for:

- what the change does and why;
- the scope of the change;
- test coverage;
- which documentation was updated.

---

## 中文说明

安装：先 `pip install ./vendor/pmd-0.1.0.tar.gz`，再 `pip install -e ".[http,dev]" httpx`；
不需要任何模型凭据。

提交前跑 `make ci`（ruff + strict mypy + 覆盖率门禁）。

代码规范：每个 `.py` 不超过 300 行；**中文 docstring 与注释，英文变量名与 API 名**；
`from __future__ import annotations` 必备；`mypy --strict` 必须通过；错误一律抛
`DMSAError` 子类；禁止 `print()`、禁止 `import *`；相对导入不超过三层。分层依赖见
`AGENTS.md`。

测试：公共 API 至少覆盖正常路径、异常路径和边界条件；关键算法要有属性测试；覆盖率
门禁 80% 不得下调；评测数字必须来自可复现的 run artifact，合成代理结果不得标成
外部评测成绩；检索必须按 user scope 约束，历史证据一律视为不可信输入。

文档：公共 API 改动同步 `docs/api-reference.md`；算法改动同步 `docs/paper-mapping.md`
与 `docs/algorithms/`；行为改动同步 `CHANGELOG.md`。

提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/)。

不要提交密钥、内网地址、私有任务数据或未脱敏的会话原文。
