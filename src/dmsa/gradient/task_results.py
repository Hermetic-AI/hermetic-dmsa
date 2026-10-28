"""任务级结果实体与持久化: 把真实任务成败与检索反馈分开记录.

任务结果由调用方如实上报, 系统不从中推断根因, 也不自动当作检索标签.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmsa.errors import DMSAError

# 任务结果允许的终态.
OUTCOMES = ("success", "failure")


class TaskResultError(DMSAError):
    """任务结果不合法或持久化失败."""


@dataclass(frozen=True)
class TaskResult:
    """一次编码任务的可验证结果."""

    task_id: str
    scope: str
    trace_id: str
    outcome: str
    tests_passed: int = 0
    files_changed: tuple[str, ...] = ()
    tokens: int = 0
    latency_ms: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        """拒绝空标识、非法终态与负数计量."""
        for identifier in (self.task_id, self.scope, self.trace_id):
            if not isinstance(identifier, str) or not identifier.strip():
                raise TaskResultError("task result requires nonempty identifiers")
        if self.outcome not in OUTCOMES:
            raise TaskResultError("task outcome must be success or failure")
        for counter in (self.tests_passed, self.tokens, self.latency_ms):
            if type(counter) is not int or counter < 0:
                raise TaskResultError("task counters must be nonnegative integers")
        if not isinstance(self.files_changed, tuple) or any(
            not isinstance(item, str) or not item for item in self.files_changed
        ):
            raise TaskResultError("files_changed must be a tuple of paths")

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的规范字段."""
        return {
            "task_id": self.task_id,
            "scope": self.scope,
            "trace_id": self.trace_id,
            "outcome": self.outcome,
            "tests_passed": self.tests_passed,
            "files_changed": list(self.files_changed),
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
        }


class TaskResultStore:
    """按 scope 隔离保存任务结果, 同一 task_id 幂等."""

    def __init__(self, path: str | Path) -> None:
        """打开数据库并初始化任务结果表."""
        try:
            self.db = sqlite3.connect(str(path), timeout=30)
            self.db.row_factory = sqlite3.Row
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS rsi_task_results (
                    scope TEXT NOT NULL, task_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                    outcome TEXT NOT NULL, tests_passed INTEGER NOT NULL,
                    files_changed TEXT NOT NULL, tokens INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL, detail TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(scope, task_id))
                """
            )
            self.db.commit()
        except sqlite3.Error as exc:
            raise TaskResultError("cannot initialize task result storage") from exc

    def record(self, result: TaskResult) -> bool:
        """保存任务结果; 同 scope 同 task_id 内容一致时幂等返回 False."""
        if not isinstance(result, TaskResult):
            raise TaskResultError("result must be TaskResult")
        body = (
            result.trace_id,
            result.outcome,
            result.tests_passed,
            json.dumps(list(result.files_changed)),
            result.tokens,
            result.latency_ms,
            result.detail,
        )
        with self.db:
            previous = self.db.execute(
                "SELECT trace_id, outcome, tests_passed, files_changed, tokens, "
                "latency_ms, detail FROM rsi_task_results "
                "WHERE scope=? AND task_id=?",
                (result.scope, result.task_id),
            ).fetchone()
            if previous is not None:
                if tuple(previous) != body:
                    raise TaskResultError("task result conflict")
                return False
            self.db.execute(
                "INSERT INTO rsi_task_results(scope, task_id, trace_id, outcome, tests_passed, "
                "files_changed, tokens, latency_ms, detail) VALUES(?,?,?,?,?,?,?,?,?)",
                (result.scope, result.task_id, *body),
            )
        return True

    def list(self, scope: str) -> tuple[TaskResult, ...]:
        """读取 scope 内的任务结果, 按创建顺序排列."""
        if not isinstance(scope, str) or not scope.strip():
            raise TaskResultError("scope must be nonempty text")
        rows = self.db.execute(
            "SELECT scope, task_id, trace_id, outcome, tests_passed, files_changed, "
            "tokens, latency_ms, detail FROM rsi_task_results WHERE scope=? "
            "ORDER BY created_at, task_id",
            (scope,),
        ).fetchall()
        return tuple(
            TaskResult(
                task_id=str(row["task_id"]),
                scope=str(row["scope"]),
                trace_id=str(row["trace_id"]),
                outcome=str(row["outcome"]),
                tests_passed=int(row["tests_passed"]),
                files_changed=tuple(json.loads(str(row["files_changed"]))),
                tokens=int(row["tokens"]),
                latency_ms=int(row["latency_ms"]),
                detail=str(row["detail"]),
            )
            for row in rows
        )

    def summary(self, scope: str) -> dict[str, int]:
        """返回 scope 内任务成功与失败计数."""
        results = self.list(scope)
        return {
            "total": len(results),
            "success": sum(1 for item in results if item.outcome == "success"),
            "failure": sum(1 for item in results if item.outcome == "failure"),
        }

    def close(self) -> None:
        """释放连接."""
        self.db.close()
