# Makefile — hermetic-dmsa
# 使用: make ci / make test / make docs / make release

.PHONY: help install install-all lint type test cov docs build clean release

help:
	@echo "hermetic-dmsa — make targets:"
	@echo "  install    — 安装项目（仅强制依赖）"
	@echo "  install-all — 安装项目 + 所有可选依赖"
	@echo "  lint       — ruff check + format 检查"
	@echo "  type       — mypy strict"
	@echo "  test       — 跑测试（无覆盖率门禁）"
	@echo "  cov        — 跑测试 + 覆盖率门禁 80%"
	@echo "  ci         — lint + type + cov（CI 同款）"
	@echo "  docs       — 构建文档站"
	@echo "  build      — 构建 wheel + sdist"
	@echo "  clean      — 清理 build 产物"
	@echo "  release    — 触发 GitHub 源码与 wheelhouse 发布（需先 git tag v*.*.*）"

install:
	pip install ./vendor/pmd-0.1.0.tar.gz
	pip install -e .

install-all:
	pip install ./vendor/pmd-0.1.0.tar.gz
	pip install -e ".[all]"

lint:
	ruff check src tests
	ruff format --check src tests

type:
	mypy src/dmsa --strict

test:
	pytest tests/ -v --no-cov

cov:
	pytest tests/ -v

ci: lint type cov

docs:
	mkdocs build --strict

build:
	python -m build

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

release:
	@if [ -z "$(VERSION)" ]; then echo "Usage: make release VERSION=0.1.0"; exit 1; fi
	git tag v$(VERSION)
	git push origin v$(VERSION)
	@echo "Tagged v$(VERSION). GitHub release will run via .github/workflows/release.yml"
