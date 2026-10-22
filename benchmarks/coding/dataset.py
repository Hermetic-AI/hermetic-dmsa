"""Coding benchmark manifests and deterministic dataset helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEVELS = ("low", "medium_low", "medium_high", "high")
LEVEL_COUNTS = {"low": 1, "medium_low": 3, "medium_high": 6, "high": 10}


@dataclass(frozen=True)
class Task:
    """带独立 gold evidence 与噪声池的合成任务。 One fixture task."""

    id: str
    kind: str
    query: str
    target_path: str
    evidence: str
    distractors: tuple[dict[str, str], ...]


def load_manifest(path: str | Path) -> dict[str, Any]:
    """载入并严格验证版本化合成任务清单。 Parse a fixture manifest."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("manifest tasks must be a nonempty list")
    noise_counts = raw.get("noise_counts", LEVEL_COUNTS)
    if (
        not isinstance(noise_counts, dict)
        or set(noise_counts) != set(LEVELS)
        or any(type(value) is not int or value < 0 for value in noise_counts.values())
    ):
        raise ValueError("noise_counts must define four nonnegative integer levels")
    ids: set[str] = set()
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError("task must be an object")
        required = ("id", "kind", "query", "target_path", "evidence", "distractors")
        if any(not isinstance(item.get(key), str) or not item[key].strip() for key in required[:5]):
            raise ValueError("task fields must be nonempty strings")
        if item["kind"] not in {"bug", "feature"}:
            raise ValueError("task kind must be bug or feature")
        if item["id"] in ids:
            raise ValueError("task IDs must be unique")
        ids.add(item["id"])
        if not isinstance(item["distractors"], list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("text"), str)
            or not isinstance(row.get("kind"), str)
            for row in item["distractors"]
        ):
            raise ValueError("distractors must contain kind/text objects")
        if max(noise_counts.values()) > len(item["distractors"]):
            raise ValueError("every task must include enough distractors for the highest level")
    return raw


def task_objects(manifest: dict[str, Any]) -> list[Task]:
    """将已校验清单转换为强类型任务。 Convert validated records."""
    return [
        Task(
            item["id"],
            item["kind"],
            item["query"],
            item["target_path"],
            item["evidence"],
            tuple(item["distractors"]),
        )
        for item in manifest["tasks"]
    ]


def _dcg(relevances: list[int]) -> float:
    """计算 DCG。 Discount cumulative gain."""
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevances))


def ndcg(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    """根据 gold evidence ID 计算 NDCG@k。 Normalize discounted gain."""
    if not relevant_ids:
        return 0.0
    actual = _dcg([int(item in relevant_ids) for item in ranked_ids])
    ideal = _dcg([1] * min(len(relevant_ids), len(ranked_ids)))
    return actual / ideal if ideal else 0.0
