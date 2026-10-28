"""端到端有效性度量: 用真实记录的轨迹、反馈与任务结果评估记忆是否真的起作用.

度量分两侧:

- 检索侧: 反馈里被确认有用的证据, 有多少真的出现在当次召回里;
- 任务侧: 真实任务结果里, 「记忆把被确认有用的证据送到了任务上」与
  「记忆没送到」两组任务的成功率分别是多少.

任务侧是观察性对比(观测谁拿到了有用证据), 不是受控 A/B, 因此报告只给分组
计数与成功率, 并显式标注口径, 不宣称因果. 样本不足或链路缺失时如实留空,
不用别的口径补齐.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmsa.gradient.policy import OfflineRSIError

# 任务侧分组口径: 观测性对比, 不等于受控对照实验.
OBSERVATION_NOTE = (
    "观察性对比: 依据真实反馈里被确认有用的证据是否真的送达任务分组, 非受控 A/B, 不代表因果结论"
)


def _loads(value: Any) -> tuple[str, ...]:
    """把 JSON 数组列解析成字符串元组; 非法内容按空处理."""
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _rate(successes: int, total: int) -> float | None:
    """返回成功率; 没有样本时如实留空."""
    return successes / total if total else None


@dataclass(frozen=True)
class TaskSample:
    """一条任务结果在有效性度量中的归类."""

    task_id: str
    outcome: str
    trace_id: str
    group: str
    confirmed_delivered: int


@dataclass(frozen=True)
class EffectivenessReport:
    """一次有效性度量的完整结果."""

    scope: str
    labeled_traces: int
    label_recall: float | None
    hit_precision: float | None
    task_total: int
    delivered_total: int
    delivered_success: int
    delivered_pass_rate: float | None
    undelivered_total: int
    undelivered_success: int
    undelivered_pass_rate: float | None
    unlabeled_total: int
    samples: tuple[TaskSample, ...]
    observation: str = OBSERVATION_NOTE

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的度量结果."""
        return {
            "scope": self.scope,
            "labeled_traces": self.labeled_traces,
            "label_recall": self.label_recall,
            "hit_precision": self.hit_precision,
            "task_total": self.task_total,
            "delivered_total": self.delivered_total,
            "delivered_success": self.delivered_success,
            "delivered_pass_rate": self.delivered_pass_rate,
            "undelivered_total": self.undelivered_total,
            "undelivered_success": self.undelivered_success,
            "undelivered_pass_rate": self.undelivered_pass_rate,
            "unlabeled_total": self.unlabeled_total,
            "observation": self.observation,
        }


def _query(db: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    """执行只读查询; 闭环表尚未建立时返回空列表, 不伪造样本."""
    try:
        return list(db.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return []


def _labeled_traces(db: sqlite3.Connection, scope: str) -> dict[str, tuple[set[str], set[str]]]:
    """读取 scope 内带反馈的轨迹, 返回轨迹 ID 到 (确认有用的证据, 召回内容)."""
    rows = _query(
        db,
        "SELECT t.id, t.hits, f.relevant FROM rsi_traces t "
        "JOIN rsi_feedback f ON f.trace_id=t.id WHERE t.scope=?",
        (scope,),
    )
    return {
        str(row["id"]): (set(_loads(row["relevant"])), set(_loads(row["hits"]))) for row in rows
    }


def measure_effectiveness(path: str | Path, scope: str) -> EffectivenessReport:
    """度量 scope 内记忆的实际有效性; 没有任何样本时各项如实留空."""
    if not isinstance(scope, str) or not scope.strip():
        raise OfflineRSIError("scope must be nonempty text")
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        labeled = _labeled_traces(db, scope)
        recalls: list[float] = []
        precisions: list[float] = []
        for relevant, hits in labeled.values():
            if not relevant:
                continue
            recalls.append(len(relevant & hits) / len(relevant))
            if hits:
                precisions.append(len(relevant & hits) / len(hits))
        samples: list[TaskSample] = []
        for row in _query(
            db,
            "SELECT task_id, trace_id, outcome FROM rsi_task_results WHERE scope=? "
            "ORDER BY created_at, task_id",
            (scope,),
        ):
            trace_id = str(row["trace_id"])
            outcome = str(row["outcome"])
            confirmed, hits = labeled.get(trace_id, (set(), set()))
            delivered = len(confirmed & hits)
            if not confirmed:
                group = "unlabeled"
            elif delivered:
                group = "delivered"
            else:
                group = "undelivered"
            samples.append(
                TaskSample(
                    task_id=str(row["task_id"]),
                    outcome=outcome,
                    trace_id=trace_id,
                    group=group,
                    confirmed_delivered=delivered,
                )
            )
    finally:
        db.close()
    delivered_samples = [item for item in samples if item.group == "delivered"]
    undelivered_samples = [item for item in samples if item.group == "undelivered"]
    unlabeled_samples = [item for item in samples if item.group == "unlabeled"]
    delivered_success = sum(1 for item in delivered_samples if item.outcome == "success")
    undelivered_success = sum(1 for item in undelivered_samples if item.outcome == "success")
    return EffectivenessReport(
        scope=scope,
        labeled_traces=len(labeled),
        label_recall=sum(recalls) / len(recalls) if recalls else None,
        hit_precision=sum(precisions) / len(precisions) if precisions else None,
        task_total=len(samples),
        delivered_total=len(delivered_samples),
        delivered_success=delivered_success,
        delivered_pass_rate=_rate(delivered_success, len(delivered_samples)),
        undelivered_total=len(undelivered_samples),
        undelivered_success=undelivered_success,
        undelivered_pass_rate=_rate(undelivered_success, len(undelivered_samples)),
        unlabeled_total=len(unlabeled_samples),
        samples=tuple(samples),
    )
