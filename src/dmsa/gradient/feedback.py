"""RSI 检索轨迹、显式标签与审计事件的持久化。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from dmsa.gradient.attribution import attribute_trace_feedback
from dmsa.gradient.policy import FailureDiagnostic, OfflineRSIError
from dmsa.gradient.replay_models import ReplayCase
from dmsa.memory import scope_key


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
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """为既有库补齐新增的轨迹列, 保证旧数据库可继续使用."""
        existing = {str(row["name"]) for row in self.db.execute("PRAGMA table_info(rsi_traces)")}
        for column in ("task_id", "corpus_hash", "repo"):
            if column not in existing:
                self.db.execute(f"ALTER TABLE rsi_traces ADD COLUMN {column} TEXT")
        self.db.commit()

    def trace(
        self,
        scope: str,
        query: str,
        top_k: int,
        hits: list[str],
        policy: str,
        *,
        task_id: str | None = None,
        corpus_hash: str = "",
        repo: str = "",
    ) -> str:
        """记录可回放查询, 含任务标识、语料指纹与仓库子域; 查询正文仅留在本地数据库."""
        trace_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO rsi_traces"
                "(id,scope,query,top_k,hits,policy,task_id,corpus_hash,repo) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    trace_id,
                    scope,
                    query,
                    top_k,
                    json.dumps(hits),
                    policy,
                    task_id,
                    corpus_hash,
                    repo,
                ),
            )
        return trace_id

    def trace_detail(self, scope: str, trace_id: str) -> dict[str, Any]:
        """读取轨迹的任务标识、语料指纹、仓库子域与命中列表."""
        row = self.db.execute(
            "SELECT task_id, corpus_hash, hits, policy, repo FROM rsi_traces "
            "WHERE id=? AND scope=?",
            (trace_id, scope),
        ).fetchone()
        if row is None:
            raise OfflineRSIError("unknown scoped trace")
        return {
            "task_id": row["task_id"],
            "corpus_hash": row["corpus_hash"],
            "hits": json.loads(str(row["hits"])),
            "policy": row["policy"],
            "repo": str(row["repo"] or ""),
        }

    def feedback(
        self, scope: str, trace_id: str, outcome: str, kind: str, relevant: list[str]
    ) -> bool:
        """保存幂等标签；失败类型由 Agent 明示，不从失败推断根因。"""
        saved, _ = self.feedback_with_attribution(scope, trace_id, outcome, kind, relevant)
        return saved

    def feedback_with_attribution(
        self, scope: str, trace_id: str, outcome: str, kind: str, relevant: list[str]
    ) -> tuple[bool, dict[str, Any]]:
        """保存幂等标签并做条目级归因, 返回是否新写入与归因摘要.

        归因只使用 Agent 明示的证据: 被点名的条目记为已确认, 明确指向召回
        内容有错的失败类型才把召回但未被点名的条目记为已反驳.
        """
        if outcome not in {"success", "failure"} or kind not in {
            "coarse_chunk",
            "exact_miss",
            "identifier_confusion",
            "missing_context",
            "missing_relation",
            "missing_structure",
            "redundancy",
            "stale_evidence",
            "unknown",
        }:
            raise OfflineRSIError("invalid feedback")
        if (
            row := self.db.execute(
                "SELECT repo FROM rsi_traces WHERE id=? AND scope=?", (trace_id, scope)
            ).fetchone()
        ) is None:
            raise OfflineRSIError("unknown scoped trace")
        partition = scope_key(scope, str(row["repo"] or ""))
        for item in relevant:
            if (
                self.db.execute(
                    "SELECT id FROM chunks WHERE id=? AND user_id=?", (item, partition)
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
                return False, {"status": "duplicate"}
            self.db.execute(
                "INSERT INTO rsi_feedback(trace_id,outcome,kind,relevant) VALUES(?,?,?,?)",
                (trace_id, *body),
            )
        summary = attribute_trace_feedback(self.db, scope, trace_id, outcome, kind, relevant)
        return True, summary

    def diagnostics(self, scope: str) -> tuple[FailureDiagnostic, ...]:
        """仅统计当前 scope 的明确失败类型与成功反例。"""
        rows = self.db.execute(
            "SELECT f.kind,sum(f.outcome='failure'),sum(f.outcome='success') "
            "FROM rsi_feedback f JOIN rsi_traces t ON t.id=f.trace_id "
            "WHERE t.scope=? AND f.kind!='unknown' GROUP BY f.kind",
            (scope,),
        ).fetchall()
        return tuple(FailureDiagnostic(str(r[0]), int(r[1]), int(r[2])) for r in rows)

    def traces(self, scope: str, limit: int = 200) -> tuple[dict[str, Any], ...]:
        """读取 scope 内的检索轨迹, 含任务标识、语料指纹、仓库子域与命中 ID."""
        if type(limit) is not int or limit < 1:
            raise OfflineRSIError("trace limit must be a positive integer")
        rows = self.db.execute(
            "SELECT id, query, top_k, hits, policy, task_id, corpus_hash, repo FROM rsi_traces "
            "WHERE scope=? ORDER BY created_at DESC LIMIT ?",
            (scope, limit),
        ).fetchall()
        return tuple(
            {
                "trace_id": str(row["id"]),
                "query": str(row["query"]),
                "top_k": int(row["top_k"]),
                "hits": tuple(json.loads(str(row["hits"]))),
                "policy": str(row["policy"]),
                "task_id": row["task_id"],
                "corpus_hash": str(row["corpus_hash"] or ""),
                "repo": str(row["repo"] or ""),
            }
            for row in rows
        )

    def diagnosis_rows(self, scope: str) -> tuple[tuple[str, str, str], ...]:
        """读取 scope 内每条反馈的失败类型、成败与切片标识."""
        rows = self.db.execute(
            "SELECT f.kind, f.outcome, COALESCE(t.task_id, t.id) FROM rsi_feedback f "
            "JOIN rsi_traces t ON t.id=f.trace_id WHERE t.scope=?",
            (scope,),
        ).fetchall()
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def labels(self, scope: str) -> dict[str, tuple[str, ...]]:
        """读取 scope 内每条轨迹已确认有用的证据 ID."""
        rows = self.db.execute(
            "SELECT t.id AS trace_id, f.relevant FROM rsi_feedback f "
            "JOIN rsi_traces t ON t.id=f.trace_id WHERE t.scope=? AND f.relevant!='[]'",
            (scope,),
        ).fetchall()
        return {str(row["trace_id"]): tuple(json.loads(str(row["relevant"]))) for row in rows}

    def cases(self, scope: str) -> tuple[ReplayCase, ...]:
        """把最新一百条显式相关来源标签转成开发回放样本。

        样本的 scope 是含仓库子域的完整分区键, 回放据此在与线上一致的
        证据集合上配对检索。
        """
        rows = self.db.execute(
            "SELECT t.id,t.query,f.relevant,f.kind,t.repo FROM rsi_traces t "
            "JOIN rsi_feedback f ON f.trace_id=t.id WHERE t.scope=? "
            "AND f.relevant!='[]' ORDER BY t.rowid DESC LIMIT 100",
            (scope,),
        ).fetchall()
        return tuple(
            ReplayCase(
                str(r[0]),
                scope_key(scope, str(r[4] or "")),
                str(r[1]),
                tuple(json.loads(r[2])),
                str(r[3]),
            )
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
