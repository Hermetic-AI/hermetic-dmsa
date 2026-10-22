"""固定 PMD 源码发行与分发元数据契约。"""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path


def test_vendored_pmd_source_integrity_and_license() -> None:
    root = Path(__file__).resolve().parents[1]
    checksum, filename = (root / "vendor" / "SHA256SUMS").read_text().split()
    archive = root / filename
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum
    with tarfile.open(archive) as source:
        names = source.getnames()
        assert "pmd-0.1.0/LICENSE" in names
        assert "pmd-0.1.0/README.md" in names
        assert "pmd-0.1.0/pyproject.toml" in names
        assert len([name for name in names if name.endswith(".py")]) == 30
        assert not any("__pycache__" in name for name in names)


def test_standalone_workflows_do_not_install_sibling_checkout() -> None:
    root = Path(__file__).resolve().parents[1]
    for workflow in (root / ".github" / "workflows").glob("*.yml"):
        assert "analysis/pmd_skeleton" not in workflow.read_text(encoding="utf-8")


def test_security_audits_the_deployed_dependency_lock() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    security = workflow.split("  security:\n", 1)[1].split("  container:\n", 1)[0]
    assert "grep -v '^pmd==' deploy/requirements.lock" in security
    assert "pip-audit --strict --require-hashes --disable-pip" in security
    assert "/tmp/dmsa-audit-requirements.lock" in security
