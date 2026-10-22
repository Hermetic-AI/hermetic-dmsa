"""确定性配对检索回放、固定门禁与离线策略注册表。."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import textwrap
from dataclasses import asdict
from pathlib import Path

from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy
from dmsa.gradient.replay_gate import gate as gate
from dmsa.gradient.replay_models import CaseResult, ReplayCase, ReplayReport
from dmsa.memory import CodingMemory, estimate_tokens

GATE_VERSION = "offline-rsi-gate-v1"
_EXECUTED_REPORTS: set[str] = set()


def _count_tokens(text: str) -> int:
    """计量 L1 当前使用的词项预算单位, 不代表真实 tokenizer token。."""
    return estimate_tokens(text)


def _digest(value: object) -> str:
    """计算规范 JSON SHA-256。."""
    try:
        normalized = value
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise OfflineRSIError("value is not canonically serializable") from exc
    return hashlib.sha256(payload.encode()).hexdigest()


def run_replay(
    memory: CodingMemory,
    baseline: RetrievalPolicy,
    candidate: RetrievalPolicy,
    cases: tuple[ReplayCase, ...],
    *,
    top_k: int,
    max_chars: int,
    max_tokens: int,
    development_only: bool = False,
) -> ReplayReport:
    """执行配对检索, 要求开发确认并检查语料快照不变。."""
    if development_only is not True:
        raise OfflineRSIError("development_only=True is required")
    if not isinstance(cases, tuple) or not cases or type(top_k) is not int or top_k < 1:
        raise OfflineRSIError("nonempty cases and positive top_k are required")
    if any(type(value) is not int or value < 1 for value in (max_chars, max_tokens)):
        raise OfflineRSIError("budgets must be positive integers")
    if any(
        not isinstance(c, ReplayCase)
        or not c.case_id or not c.user_id or not c.query or not c.slice or not c.relevant_ids
        for c in cases
    ):
        raise OfflineRSIError("malformed replay case")
    if len({c.case_id for c in cases}) != len(cases):
        raise OfflineRSIError("case IDs must be unique")
    if baseline.search.max_chars != max_chars or candidate.search.max_chars != max_chars:
        raise OfflineRSIError("max_chars budget must match both policies")
    if baseline.search.max_tokens != max_tokens or candidate.search.max_tokens != max_tokens:
        raise OfflineRSIError("max_tokens budget must match both policies")
    users = tuple(sorted({c.user_id for c in cases}))
    state = memory.corpus_state(users)

    def execute(policy: RetrievalPolicy) -> tuple[CaseResult, ...]:
        output = []
        for case in cases:
            hits = memory.search(case.query, case.user_id, top_k, policy.search)
            chars = sum(len(hit.content) for hit in hits)
            tokens = sum(_count_tokens(hit.content) for hit in hits)
            if chars > max_chars or tokens > max_tokens:
                raise OfflineRSIError("retrieval exceeded declared evidence budget")
            output.append(
                CaseResult(
                    case.case_id,
                    tuple(hit.id for hit in hits),
                    case.relevant_ids,
                    case.slice,
                    chars,
                    tokens,
                )
            )
        return tuple(output)

    before, after = execute(baseline), execute(candidate)
    if memory.corpus_state(users) != state:
        raise OfflineRSIError("corpus changed during paired replay")
    baseline_hash = _digest(asdict(baseline.search))
    candidate_hash = _digest(asdict(candidate.search))
    report = ReplayReport(
        baseline_hash,
        candidate_hash,
        state.fingerprint,
        _digest([asdict(case) for case in cases]),
        top_k,
        max_chars,
        max_tokens,
        before,
        after,
    )
    _EXECUTED_REPORTS.add(report.report_hash)
    return report


class PolicyRegistry:
    """使用 SQLite 原子保存候选、裁决和活动版本。."""

    def __init__(self, path: str | Path) -> None:
        """打开注册表并初始化事务表。."""
        try:
            self.db = sqlite3.connect(str(path))
            self.db.execute(
                textwrap.dedent("""\
                CREATE TABLE IF NOT EXISTS policies (
                    version TEXT PRIMARY KEY, body TEXT NOT NULL,
                    state TEXT NOT NULL, report TEXT
                )""")
            )
            self.db.execute(
                textwrap.dedent("""\
                CREATE TABLE IF NOT EXISTS active (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version TEXT NOT NULL
                )""")
            )
            self.db.execute(
                textwrap.dedent("""\
                CREATE TABLE IF NOT EXISTS history (
                    event INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL, version TEXT NOT NULL
                )""")
            )
            self.db.commit()
        except sqlite3.Error as exc:
            raise OfflineRSIError("cannot initialize policy registry") from exc
        except OSError as exc:
            raise OfflineRSIError("cannot open policy registry") from exc

    def register(
        self, policy: RetrievalPolicy, report: ReplayReport, *, allow_bootstrap: bool = False
    ) -> str:
        """保存候选并按内置门禁原子接受或拒绝。."""
        if report.report_hash not in _EXECUTED_REPORTS or report.candidate_hash != _digest(
            asdict(policy.search)
        ):
            raise OfflineRSIError("only locally executed replay evidence can be registered")
        version = _digest((asdict(policy.search), report.report_hash))
        status = "accepted" if gate(report) else "rejected"
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                active = self.db.execute(
                    "SELECT p.body FROM active a JOIN policies p "
                    "ON p.version=a.version WHERE a.singleton=1"
                ).fetchone()
                if status == "accepted":
                    if active is None and not allow_bootstrap:
                        raise OfflineRSIError("initial activation requires allow_bootstrap=True")
                    if active is not None and allow_bootstrap:
                        raise OfflineRSIError("bootstrap is only valid before first activation")
                    if (
                        active is not None
                        and _digest(json.loads(active[0])) != report.baseline_hash
                    ):
                        raise OfflineRSIError("replay baseline is stale relative to active policy")
                self.db.execute(
                    "INSERT OR REPLACE INTO policies VALUES (?, ?, ?, ?)",
                    (
                        version,
                        json.dumps(policy.search.__dict__, sort_keys=True),
                        status,
                        json.dumps(report.to_dict(), sort_keys=True),
                    ),
                )
                if status == "accepted":
                    self.db.execute("INSERT OR REPLACE INTO active VALUES (1, ?)", (version,))
                self.db.execute(
                    "INSERT INTO history(action, version) VALUES (?, ?)", (status, version)
                )
        except OfflineRSIError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise OfflineRSIError("registry transaction failed") from exc
        return version

    def promote_replay(self, policy: RetrievalPolicy, report: ReplayReport) -> str:
        """只接收同策略回放, 存储前复核证据摘要。."""
        if report.candidate_hash != _digest(asdict(policy.search)) or report.report_hash != _digest(
            report.to_dict()
        ):
            raise OfflineRSIError("replay evidence hash mismatch")
        return self.register(policy, report)

    def rollback(self, version: str) -> None:
        """切回已接受版本。."""
        try:
            with self.db:
                row = self.db.execute(
                    "SELECT state FROM policies WHERE version=?", (version,)
                ).fetchone()
                if row is None or row[0] != "accepted":
                    raise OfflineRSIError("unknown or unaccepted rollback version")
                self.db.execute("INSERT OR REPLACE INTO active VALUES (1, ?)", (version,))
                self.db.execute(
                    "INSERT INTO history(action, version) VALUES ('rollback', ?)", (version,)
                )
        except sqlite3.Error as exc:
            raise OfflineRSIError("registry rollback failed") from exc

    @property
    def active_version(self) -> str | None:
        """读取当前活动版本。."""
        try:
            row = self.db.execute("SELECT version FROM active WHERE singleton=1").fetchone()
            return str(row[0]) if row else None
        except sqlite3.Error as exc:
            raise OfflineRSIError("cannot read active policy") from exc

    def get(self, version: str) -> RetrievalPolicy:
        """按版本加载不可变策略。."""
        try:
            row = self.db.execute(
                "SELECT body FROM policies WHERE version=?", (version,)
            ).fetchone()
            if row is None:
                raise OfflineRSIError("unknown policy version")
            return RetrievalPolicy.from_dict(json.loads(str(row[0])))
        except OfflineRSIError:
            raise
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise OfflineRSIError("stored policy is malformed") from exc

    def close(self) -> None:
        """关闭 SQLite 连接。."""
        try:
            self.db.close()
        except sqlite3.Error as exc:
            raise OfflineRSIError("cannot close policy registry") from exc

    @property
    def history(self) -> tuple[tuple[str, str], ...]:
        """返回候选裁决与回滚事件。."""
        try:
            rows = self.db.execute("SELECT action, version FROM history ORDER BY event").fetchall()
            return tuple((str(row[0]), str(row[1])) for row in rows)
        except sqlite3.Error as exc:
            raise OfflineRSIError("cannot read registry history") from exc
