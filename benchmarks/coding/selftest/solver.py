"""按检索证据真实打补丁并运行 pytest 的本地求解器."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BLOCK_RE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]+path=([^\s`]+)\n(.*?)```", re.S)
ATTEMPT_TIMEOUT = 60.0


@dataclass(frozen=True)
class SolveResult:
    """一次求解的真实测试结果与尝试轨迹."""

    resolved: bool
    attempts: int
    localized_path: str | None
    applied_rank: int | None
    failures: tuple[str, ...]


def _safe_target(root: Path, path: str) -> Path | None:
    """拒绝绝对路径与越出仓库的相对路径."""
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def tests_pass(root: Path, test_file: str) -> bool:
    """在隔离目录运行指定测试; 超时或启动失败一律视为未通过."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", test_file],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=ATTEMPT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def solve(
    evidence: list[str], root: Path, test_file: str, max_attempts: int = 3
) -> SolveResult:
    """按排名尝试证据中的完整代码块; 只有测试通过才算解决."""
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    seen: set[tuple[str, str]] = set()
    attempts = 0
    failures: list[str] = []
    for rank, content in enumerate(evidence, 1):
        for path, body in BLOCK_RE.findall(content):
            target = _safe_target(root, path)
            if target is None or not target.exists() or (path, body) in seen:
                continue
            seen.add((path, body))
            target.write_text(body, encoding="utf-8")
            attempts += 1
            if tests_pass(root, test_file):
                return SolveResult(True, attempts, path, rank, tuple(failures))
            failures.append(path)
            if attempts >= max_attempts:
                return SolveResult(False, attempts, path, None, tuple(failures))
    return SolveResult(False, attempts, None, None, tuple(failures))


def substitute(text: str, substitutions: tuple[tuple[str, str], ...]) -> str:
    """把同族历史里的标识符映射到当前任务."""
    for source, target in substitutions:
        text = text.replace(source, target)
    return text


def solve_by_analogy(
    evidence: list[str],
    root: Path,
    test_file: str,
    substitutions: tuple[tuple[str, str], ...],
    max_attempts: int = 3,
) -> SolveResult:
    """把同族历史补丁按标识符映射后应用; 只有测试通过才算迁移成功.

    该代理指标测量检索是否给到了可迁移的修复经验, 不代表真实 Agent 的解题率。
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    seen: set[tuple[str, str]] = set()
    attempts = 0
    failures: list[str] = []
    for rank, content in enumerate(evidence, 1):
        for path, body in BLOCK_RE.findall(content):
            mapped_path = substitute(path, substitutions)
            mapped_body = substitute(body, substitutions)
            target = _safe_target(root, mapped_path)
            if target is None or not target.exists() or (mapped_path, mapped_body) in seen:
                continue
            seen.add((mapped_path, mapped_body))
            target.write_text(mapped_body, encoding="utf-8")
            attempts += 1
            if tests_pass(root, test_file):
                return SolveResult(True, attempts, mapped_path, rank, tuple(failures))
            failures.append(mapped_path)
            if attempts >= max_attempts:
                return SolveResult(False, attempts, mapped_path, None, tuple(failures))
    return SolveResult(False, attempts, None, None, tuple(failures))
