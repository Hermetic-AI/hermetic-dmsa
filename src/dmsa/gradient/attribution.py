"""条目级因果归因: 把显式反馈与任务结果映射到记忆条目的信任更新.

归因只消费 Agent 明示的信号, 不从失败反推根因:

- 反馈中被点名的条目记为「已确认」;
- 失败类型明确指向召回内容本身有错时, 召回但未被点名的条目记为「已反驳」;
- 「证据不足」类失败(没召回到、缺上下文、缺关系等)不构成对召回条目的反驳,
  因为缺什么和召回错了是两回事;
- 任务成功把该轨迹召回的条目记为「已验证」, 任务失败不产生任何归因.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from dmsa.memory import apply_verdict, ensure_stats_table, scope_key

# 明确指向「召回内容本身有错」的失败类型, 只有这些才对召回条目记反驳.
CONTRADICTING_KINDS: frozenset[str] = frozenset(
    {"coarse_chunk", "identifier_confusion", "stale_evidence"}
)


@dataclass(frozen=True)
class Attribution:
    """一次归因落到条目上的计数增量."""

    partition: str
    confirmed: tuple[str, ...] = ()
    contradicted: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """判断本次归因是否没有任何条目级更新."""
        return not (self.confirmed or self.contradicted or self.verified)

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的归因摘要."""
        return {
            "partition": self.partition,
            "confirmed": list(self.confirmed),
            "contradicted": list(self.contradicted),
            "verified": list(self.verified),
        }


def attribute_feedback(
    partition: str,
    recalled: Iterable[str],
    relevant: Iterable[str],
    outcome: str,
    kind: str,
) -> Attribution:
    """把一次显式检索反馈映射到条目级归因.

    ``recalled`` 是该次检索真正返回的条目, ``relevant`` 是 Agent 确认有用的条目.
    失败类型只有落在 :data:`CONTRADICTING_KINDS` 内才会反驳召回条目.
    """
    confirmed = tuple(sorted(set(relevant)))
    contradicted: tuple[str, ...] = ()
    if outcome == "failure" and kind in CONTRADICTING_KINDS:
        contradicted = tuple(sorted(set(recalled) - set(confirmed)))
    return Attribution(partition=partition, confirmed=confirmed, contradicted=contradicted)


def attribute_task(partition: str, recalled: Iterable[str], outcome: str) -> Attribution:
    """把任务结果映射到条目级归因: 成功验证召回, 失败不归因.

    任务失败往往来自任务本身或环境, 无法定位到某条记忆, 因此失败不产生反驳.
    """
    if outcome != "success":
        return Attribution(partition=partition)
    return Attribution(partition=partition, verified=tuple(sorted(set(recalled))))


def persist_attribution(db: sqlite3.Connection, attribution: Attribution) -> int:
    """把归因结果写入条目统计, 返回被更新的条目数."""
    if attribution.empty:
        return 0
    ensure_stats_table(db)
    updated = 0
    with db:
        updated += apply_verdict(db, attribution.partition, attribution.confirmed, confirmed=True)
        updated += apply_verdict(
            db, attribution.partition, attribution.contradicted, contradicted=True
        )
        updated += apply_verdict(db, attribution.partition, attribution.verified, verified=True)
    return updated


def trace_recall(db: sqlite3.Connection, scope: str, trace_id: str) -> tuple[str, tuple[str, ...]]:
    """读取轨迹的仓库子域与召回条目; 轨迹不存在时返回空分区与空列表."""
    try:
        row = db.execute(
            "SELECT repo, hits FROM rsi_traces WHERE id=? AND scope=?", (trace_id, scope)
        ).fetchone()
    except sqlite3.Error:
        return "", ()
    if row is None:
        return "", ()
    try:
        hits = json.loads(str(row["hits"] or "[]"))
    except (TypeError, ValueError):
        hits = []
    recalled = tuple(str(item) for item in hits) if isinstance(hits, list) else ()
    return str(row["repo"] or ""), recalled


def attribute_trace_feedback(
    db: sqlite3.Connection,
    scope: str,
    trace_id: str,
    outcome: str,
    kind: str,
    relevant: Iterable[str],
) -> dict[str, Any]:
    """按轨迹召回内容给一次反馈做归因, 返回可上报的摘要."""
    repo, recalled = trace_recall(db, scope, trace_id)
    if not recalled:
        return {"status": "no_recall", "confirmed": [], "contradicted": [], "verified": []}
    partition = scope_key(scope, repo)
    attribution = attribute_feedback(partition, recalled, relevant, outcome, kind)
    applied = persist_attribution(db, attribution)
    return {"status": "applied", "applied": applied, **attribution.to_dict()}


def attribute_trace_task(
    db: sqlite3.Connection, scope: str, trace_id: str, outcome: str
) -> dict[str, Any]:
    """按轨迹召回内容给一次任务结果做归因, 返回可上报的摘要."""
    repo, recalled = trace_recall(db, scope, trace_id)
    if not recalled:
        return {"status": "no_recall", "confirmed": [], "contradicted": [], "verified": []}
    partition = scope_key(scope, repo)
    attribution = attribute_task(partition, recalled, outcome)
    applied = persist_attribution(db, attribution)
    return {"status": "applied", "applied": applied, **attribution.to_dict()}
