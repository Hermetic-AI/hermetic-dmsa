"""mem0 对照臂: 相同任务、相同 top_k 下运行, 依赖缺失时如实输出 null 与原因."""

from __future__ import annotations

import os
from collections.abc import Sequence
from importlib import import_module, util
from typing import Any

from benchmarks.coding.dataset import LEVEL_COUNTS, ndcg
from benchmarks.coding.holdout_tasks import NOISE_LEVEL, SPLITS, TOP_K, HoldoutTask


def mem0_metrics(
    tasks: Sequence[HoldoutTask], split_of: dict[str, str], top_k: int
) -> dict[str, Any]:
    """在相同任务与相同 top_k 下运行 mem0, 只统计检索指标."""
    # mem0 是可选依赖, 用 importlib 动态导入避免类型检查期缺少 stubs
    client = import_module("mem0").Memory()
    metrics: dict[str, Any] = {}
    for split in SPLITS:
        rows = [task for task in tasks if split_of[task.family] == split]
        if not rows:
            metrics[split] = None
            continue
        recalls: list[float] = []
        scores: list[float] = []
        for task in rows:
            scope = f"mem0-{task.task_id}"
            client.add(task.gold, user_id=scope)
            for text in task.distractors[: LEVEL_COUNTS[NOISE_LEVEL]]:
                client.add(text, user_id=scope)
            results = client.search(task.query, user_id=scope, limit=top_k)
            ranked = [str(row.get("memory", "")) for row in results]
            classes = ["gold" if task.gold[:40] in item else "noise" for item in ranked]
            recalls.append(1.0 if "gold" in classes else 0.0)
            scores.append(ndcg(classes, {"gold"}))
            client.delete_all(user_id=scope)
        metrics[split] = {
            "task_count": len(rows),
            "evidence_recall": sum(recalls) / len(recalls),
            "ndcg": sum(scores) / len(scores),
        }
    return metrics


def mem0_arm(
    tasks: Sequence[HoldoutTask], split_of: dict[str, str], top_k: int = TOP_K
) -> dict[str, Any]:
    """mem0 对照臂; 依赖缺失或未开启时如实输出 null 与原因."""
    empty: dict[str, Any] = dict.fromkeys(SPLITS)
    if util.find_spec("mem0") is None:
        return {"available": False, "reason": "mem0 依赖未安装", "metrics": empty}
    if os.environ.get("DMSA_BENCH_MEM0") != "1":
        return {
            "available": False,
            "reason": "mem0 依赖可用但未开启对照 (设置 DMSA_BENCH_MEM0=1)",
            "metrics": empty,
        }
    try:
        return {
            "available": True,
            "reason": None,
            "metrics": mem0_metrics(tasks, split_of, top_k),
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": f"mem0 对照运行失败: {type(exc).__name__}",
            "metrics": empty,
        }
