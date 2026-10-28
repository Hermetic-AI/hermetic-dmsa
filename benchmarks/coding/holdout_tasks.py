"""留出评测的语料构造与切分: 任务族、三分分配与稳定噪声抽样."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from benchmarks.coding.ablation import TASKS as ABLATION_TASKS
from benchmarks.coding.dataset import load_manifest, task_objects

SPLITS = ("evolution_train", "replay_validation", "heldout_test")
TOP_K = 5
NOISE_LEVEL = "medium_high"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mini.json"
_SUFFIX_RE = re.compile(r"-\d+$")


@dataclass(frozen=True)
class HoldoutTask:
    """一条留出评测任务: 查询、gold 证据、噪声池与所属任务族."""

    task_id: str
    family: str
    query: str
    gold: str
    distractors: tuple[str, ...]


def fixture_tasks(manifest_path: str | Path = FIXTURE_PATH) -> tuple[HoldoutTask, ...]:
    """把官方风格的合成清单转成留出任务, 任务族取去掉序号的 ID."""
    tasks = task_objects(load_manifest(manifest_path))
    return tuple(
        HoldoutTask(
            task_id=item.id,
            family=_SUFFIX_RE.sub("", item.id),
            query=item.query,
            gold=item.evidence,
            distractors=tuple(row["text"] for row in item.distractors),
        )
        for item in tasks
    )


def ablation_tasks() -> tuple[HoldoutTask, ...]:
    """复用消融对照集, 任务族显式加前缀避免与 fixture 合并."""
    return tuple(
        HoldoutTask(
            task_id=item.task_id,
            family=f"ablation-{item.task_id}",
            query=item.query,
            gold=item.gold,
            distractors=item.distractors,
        )
        for item in ABLATION_TASKS
    )


def corpus(manifest_path: str | Path = FIXTURE_PATH) -> tuple[HoldoutTask, ...]:
    """返回去重后的全部留出任务."""
    tasks = (*fixture_tasks(manifest_path), *ablation_tasks())
    seen: set[str] = set()
    unique: list[HoldoutTask] = []
    for task in tasks:
        if task.task_id in seen:
            continue
        seen.add(task.task_id)
        unique.append(task)
    return tuple(unique)


def assign(families: Iterable[str], seed: int = 0) -> dict[str, str]:
    """按任务族确定性分层分配切分; 同族任务永远落在同一切分.

    先按 (种子, 任务族) 的哈希排序, 再轮转落位, 这样在任务族数量足够时
    三个切分都不会为空; 分配只取决于任务族集合与种子, 复跑一致.
    """
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    unique = list(dict.fromkeys(families))
    ordered = sorted(
        unique,
        key=lambda family: (hashlib.sha256(f"{seed}:{family}".encode()).hexdigest(), family),
    )
    return {family: SPLITS[index % len(SPLITS)] for index, family in enumerate(ordered)}


def select_noise(task: HoldoutTask, seed: int, count: int) -> tuple[str, ...]:
    """按种子稳定选取噪声子集, 保证复跑一致."""
    ordered = sorted(
        task.distractors,
        key=lambda text: hashlib.sha256(f"{seed}:{task.task_id}:{text}".encode()).hexdigest(),
    )
    return tuple(ordered[:count])


def scope_of(task_id: str) -> str:
    """返回任务独占的评测 scope 名."""
    return f"holdout-{task_id}"
