"""外部 Coding Agent 适配: 命令直接改仓库, 判分只认本地 pytest."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentRun:
    """一次外部 Agent 执行的观测结果."""

    return_code: int
    latency_seconds: float
    stdout: str
    stderr: str
    claimed_resolved: bool | None = None
    claimed_path: str | None = None


def _claims(stdout: str) -> tuple[bool | None, str | None]:
    """解析 Agent 自述; 解析失败只影响诊断, 不影响判分."""
    try:
        payload: Any = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    resolved = payload.get("resolved")
    path = payload.get("localized_path")
    return (
        resolved if type(resolved) is bool else None,
        path if isinstance(path, str) else None,
    )


def run_agent(
    argv: list[str], payload: dict[str, Any], timeout: float = 600.0
) -> AgentRun:
    """以 JSON stdin 调用外部 Agent; 不经 shell, 只允许改仓库目录."""
    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ValueError("agent argv must be a nonempty list of strings")
    if timeout <= 0:
        raise ValueError("agent timeout must be positive")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("agent command failed or timed out") from exc
    claimed_resolved, claimed_path = _claims(completed.stdout)
    return AgentRun(
        completed.returncode,
        time.perf_counter() - started,
        completed.stdout[-2000:],
        completed.stderr[-2000:],
        claimed_resolved,
        claimed_path,
    )
