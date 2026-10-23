# ruff: noqa: D415, RUF002
"""RSI 检索轨迹、显式标签与审计事件的持久化。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from dmsa.gradient.policy import FailureDiagnostic, OfflineRSIError
from dmsa.gradient.replay_models import ReplayCase


class RetrievalFeedbackStore:
    """每次请求独立连接，同一 SQLite 保存记忆与 RSI 运行证据。"""

    def __init__(self, path: str | Path) -> None:
        """用新增表初始化闭环状态，保留旧实验表。"""
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rsi_traces (
                id TEXT PRIMARY KEY, scope TEXT, query TEXT, top_k INTEGER,
                hits TEXT, policy TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rsi_feedback (
                trace_id TEXT PRIMARY KEY, outcome TEXT, kind TEXT, relevant TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rsi_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, kind TEXT,
                payload TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rsi_versions (
                scope TEXT, version TEXT, policy TEXT, PRIMARY KEY(scope, version));
            CREATE TABLE IF NOT EXISTS rsi_active (scope TEXT PRIMARY KEY, version TEXT);
        """)
        self.db.commit()

    def trace(self, scope: str, query: str, top_k: int, hits: list[str], policy: str) -> str:
        """记录可回放查询，查询正文仅留在本地数据库。"""
        trace_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO rsi_traces(id,scope,query,top_k,hits,policy) VALUES(?,?,?,?,?,?)",
                (trace_id, scope, query, top_k, json.dumps(hits), policy),
            )
        return trace_id

    def feedback(
        self, scope: str, trace_id: str, outcome: str, kind: str, relevant: list[str]
    ) -> bool:
        """保存幂等标签；失败类型由 Agent 明示，不从失败推断根因。"""
        if outcome not in {"success", "failure"} or kind not in {
            "unknown",
            "exact_miss",
            "redundancy",
            "missing_context",
        }:
            raise OfflineRSIError("invalid feedback")
        if (
            self.db.execute(
                "SELECT id FROM rsi_traces WHERE id=? AND scope=?", (trace_id, scope)
            ).fetchone()
            is None
        ):
            raise OfflineRSIError("unknown scoped trace")
        for item in relevant:
            if (
                self.db.execute(
                    "SELECT id FROM chunks WHERE id=? AND user_id=?", (item, scope)
                ).fetchone()
                is None
            ):
                raise OfflineRSIError("unknown scoped relevant evidence")
        body = (outcome, kind, json.dumps(sorted(set(relevant))))
        with self.db:
            previous = self.db.execute(
                "SELECT outcome,kind,relevant FROM rsi_feedback WHERE trace_id=?", (trace_id,)
            ).fetchone()
            if previous:
                if tuple(previous) != body:
                    raise OfflineRSIError("feedback conflict")
                return False
            self.db.execute(
                "INSERT INTO rsi_feedback(trace_id,outcome,kind,relevant) VALUES(?,?,?,?)",
                (trace_id, *body),
            )
        return True

    def diagnostics(self, scope: str) -> tuple[FailureDiagnostic, ...]:
        """仅统计当前 scope 的明确失败类型与成功反例。"""
        rows = self.db.execute(
            "SELECT f.kind,sum(f.outcome='failure'),sum(f.outcome='success') "
            "FROM rsi_feedback f JOIN rsi_traces t ON t.id=f.trace_id "
            "WHERE t.scope=? AND f.kind!='unknown' GROUP BY f.kind",
            (scope,),
        ).fetchall()
        return tuple(FailureDiagnostic(str(r[0]), int(r[1]), int(r[2])) for r in rows)

    def cases(self, scope: str) -> tuple[ReplayCase, ...]:
        """把最新一百条显式相关来源标签转成开发回放样本。"""
        rows = self.db.execute(
            "SELECT t.id,t.query,f.relevant,f.kind FROM rsi_traces t "
            "JOIN rsi_feedback f ON f.trace_id=t.id WHERE t.scope=? "
            "AND f.relevant!='[]' ORDER BY t.rowid DESC LIMIT 100",
            (scope,),
        ).fetchall()
        return tuple(
            ReplayCase(str(r[0]), scope, str(r[1]), tuple(json.loads(r[2])), str(r[3]))
            for r in rows
        )

    def event(self, scope: str, kind: str, payload: dict[str, Any]) -> None:
        """保存审计事件，不记录密钥及 provider 原始错误。"""
        with self.db:
            self.db.execute(
                "INSERT INTO rsi_events(scope,kind,payload) VALUES(?,?,?)",
                (scope, kind, json.dumps(payload, ensure_ascii=False)),
            )

    def close(self) -> None:
        """释放连接。"""
        self.db.close()
