"""记忆有效性的配对对照: 同一批任务上比较「有记忆」与「无记忆」两组表现.

对照是配对的: 每个任务在两组里各跑一次, 因此差异来自同一批任务, 而不是两批
不同任务. 没有配置真实 Agent 时只报告检索侧指标, 任务侧一律留空, 不用检索
指标冒充解题结果. 语料是自建合成任务, 结果只作开发期代理指标.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.coding.dataset import load_manifest
from benchmarks.coding.evaluator import evaluate_fixture

DEFAULT_MANIFEST = Path("benchmarks/coding/fixtures/mini.json")
DEFAULT_ARM_ON = "sparse"
DEFAULT_ARM_OFF = "no_memory"


def _index_rows(report: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """按任务 ID 索引各臂的结果行."""
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for level_rows in report.get("noise_levels", {}).values():
        for row in level_rows:
            indexed.setdefault(str(row["task_id"]), {})[str(row["arm"])] = row
    return indexed


def _rate(values: list[bool]) -> float | None:
    """返回布尔样本的成功率; 没有样本时如实留空."""
    return sum(values) / len(values) if values else None


def paired_comparison(
    report: dict[str, Any], *, arm_on: str = DEFAULT_ARM_ON, arm_off: str = DEFAULT_ARM_OFF
) -> dict[str, Any]:
    """从评测报告里提取配对对照结果; 缺一侧结果的样本不参与配对."""
    indexed = _index_rows(report)
    on_pass: list[bool] = []
    off_pass: list[bool] = []
    on_resolved: list[bool] = []
    off_resolved: list[bool] = []
    wins = losses = ties = 0
    recall_on: list[float] = []
    recall_off: list[float] = []
    for arms in indexed.values():
        on_row = arms.get(arm_on)
        off_row = arms.get(arm_off)
        if on_row is None or off_row is None:
            continue
        recall_on.append(float(on_row["evidence_recall"]))
        recall_off.append(float(off_row["evidence_recall"]))
        on_result = on_row.get("agent_result")
        off_result = off_row.get("agent_result")
        if on_result is None or off_result is None:
            continue
        on_pass.append(bool(on_result["test_passed"]))
        off_pass.append(bool(off_result["test_passed"]))
        on_resolved.append(bool(on_result["resolved"]))
        off_resolved.append(bool(off_result["resolved"]))
        if on_result["test_passed"] == off_result["test_passed"]:
            ties += 1
        elif on_result["test_passed"]:
            wins += 1
        else:
            losses += 1
    on_rate = _rate(on_pass)
    off_rate = _rate(off_pass)
    on_resolved_rate = _rate(on_resolved)
    off_resolved_rate = _rate(off_resolved)
    return {
        "arm_on": arm_on,
        "arm_off": arm_off,
        "paired_tasks": len(on_pass),
        "memory_on_test_pass_rate": on_rate,
        "memory_off_test_pass_rate": off_rate,
        "test_pass_rate_delta": None if on_rate is None or off_rate is None else on_rate - off_rate,
        "memory_on_resolved_rate": on_resolved_rate,
        "memory_off_resolved_rate": off_resolved_rate,
        "resolved_rate_delta": (
            None
            if on_resolved_rate is None or off_resolved_rate is None
            else on_resolved_rate - off_resolved_rate
        ),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "evidence_recall_on": sum(recall_on) / len(recall_on) if recall_on else None,
        "evidence_recall_off": sum(recall_off) / len(recall_off) if recall_off else None,
        "note": (
            "配对对照, 同一批任务在两臂各跑一次; 任务侧指标依赖真实 Agent, "
            "未配置时留空; 语料为自建合成任务, 不是官方成绩"
        ),
    }


def run_paired(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    *,
    arm_on: str = DEFAULT_ARM_ON,
    arm_off: str = DEFAULT_ARM_OFF,
    seed: int = 0,
    agent: Any = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """运行两臂评测并返回配对对照结果."""
    manifest = load_manifest(manifest_path)
    report = evaluate_fixture(
        manifest,
        arms=[arm_off, arm_on],
        seed=seed,
        agent=agent,
        agent_name=agent_name,
    )
    return {
        "schema_version": 1,
        "evaluation_type": "synthetic_proxy_not_official",
        "official_score": None,
        "paired": paired_comparison(report, arm_on=arm_on, arm_off=arm_off),
        "run_metadata": report["run_metadata"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """按参数运行配对对照, 结果写 UTF-8 文件或打印到标准输出."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="合成任务清单路径")
    parser.add_argument("--arm-on", default=DEFAULT_ARM_ON, help="开启记忆的臂")
    parser.add_argument("--arm-off", default=DEFAULT_ARM_OFF, help="关闭记忆的臂")
    parser.add_argument("--seed", type=int, default=0, help="噪声抽样种子")
    parser.add_argument("--output", help="可选 JSON 输出路径; 缺省打印到标准输出")
    args = parser.parse_args(argv)
    payload = json.dumps(
        run_paired(args.manifest, arm_on=args.arm_on, arm_off=args.arm_off, seed=args.seed),
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
