"""按任务族切分的留出评测入口: 训练 / 回放 / 留出三分, 附 mem0 对照臂.

评测语料仍然是自建合成任务(官方 fixture + 消融对照集), 因此结果只能作为
开发期代理指标, 不冒充官方成绩. 切分的目的只有一个: 让"用来产生策略改动的
任务"与"用来报告效果的任务"不重叠, 使泄漏在结果里可见.

语料与切分在 `holdout_tasks`, 训练侧指标与候选产生在 `holdout_evolution`,
对照臂在 `holdout_mem0`; 本模块只负责编排与命令行输出.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.coding.holdout_evolution import evolve, metrics, populate
from benchmarks.coding.holdout_mem0 import mem0_arm
from benchmarks.coding.holdout_tasks import (
    FIXTURE_PATH,
    NOISE_LEVEL,
    SPLITS,
    TOP_K,
    assign,
    corpus,
)
from dmsa.memory import CodingMemory, SearchPolicy


def run_holdout(
    seed: int = 0, manifest_path: str | Path = FIXTURE_PATH, top_k: int = TOP_K
) -> dict[str, Any]:
    """执行三分留出评测, 返回基线、候选与对照臂的真实指标."""
    tasks = corpus(manifest_path)
    split_of = assign((task.family for task in tasks), seed)
    grouped = {
        split: tuple(task for task in tasks if split_of[task.family] == split) for split in SPLITS
    }
    with tempfile.TemporaryDirectory() as raw:
        memory = CodingMemory(Path(raw) / "holdout.sqlite", policy=SearchPolicy(dense_weight=0.0))
        try:
            gold = populate(memory, tasks, seed)
            evolution = evolve(memory, grouped["evolution_train"], gold)
            candidate = SearchPolicy(**evolution["candidate"])
            baseline_metrics = {
                split: metrics(memory, gold, grouped[split], memory.policy, top_k)
                for split in SPLITS
            }
            candidate_metrics = {
                split: metrics(memory, gold, grouped[split], candidate, top_k) for split in SPLITS
            }
        finally:
            memory.close()
    return {
        "schema_version": 1,
        "evaluation_type": "synthetic_proxy_not_official",
        "official_score": None,
        "seed": seed,
        "top_k": top_k,
        "noise_level": NOISE_LEVEL,
        "splits": {split: [task.task_id for task in grouped[split]] for split in SPLITS},
        "families": dict(split_of),
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "evolution": evolution,
        "mem0": mem0_arm(tasks, split_of, top_k),
        "note": "自建合成任务的三分留出评测; 留出切分不参与策略产生, 也不代表官方成绩",
    }


def main(argv: Sequence[str] | None = None) -> int:
    """按参数运行留出评测, 结果写 UTF-8 文件或打印到标准输出."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0, help="任务族切分与噪声抽样种子")
    parser.add_argument("--manifest", default=str(FIXTURE_PATH), help="官方 fixture 路径")
    parser.add_argument("--top-k", type=int, default=TOP_K, help="检索返回上限")
    parser.add_argument("--output", help="可选 JSON 输出路径; 缺省打印到标准输出")
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    payload = json.dumps(
        run_holdout(seed=args.seed, manifest_path=args.manifest, top_k=args.top_k),
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        return 0
    # Windows 控制台默认 cp936, 不重配会把中文说明打成乱码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
