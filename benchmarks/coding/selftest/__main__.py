"""命令行入口: 生成本地任务并输出 JSON/Markdown 报告."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.coding.selftest.cases import NOISE_LEVELS
from benchmarks.coding.selftest.runner import HISTORY_MODES, render_markdown, run_selftest
from dmsa.observability.logging import get_logger


def main(argv: list[str] | None = None) -> int:
    """解析参数、运行闭环并按需在退化时返回非零退出码."""
    parser = argparse.ArgumentParser(prog="python -m benchmarks.coding.selftest")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--arms", nargs="+", choices=("baseline", "tuned"), default=["baseline", "tuned"]
    )
    parser.add_argument("--levels", nargs="+", choices=NOISE_LEVELS, default=list(NOISE_LEVELS))
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--out", default="benchmarks/results/coding-selftest.json")
    parser.add_argument(
        "--history",
        choices=HISTORY_MODES,
        default="case",
        help=("case: 历史含标准答案; analogy: 历史是同族异标识符的已解任务; none: 无历史对照"),
    )
    parser.add_argument(
        "--agent-argv",
        default=None,
        help="外部 Coding Agent 命令的 JSON 数组; Agent 直接改仓库, 判分只用本地 pytest",
    )
    parser.add_argument("--agent-timeout", type=float, default=600.0)
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="tuned 的解决率低于 baseline 时返回非零退出码",
    )
    args = parser.parse_args(argv)
    if args.seed < 0:
        parser.error("--seed must be nonnegative")
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    if args.agent_timeout <= 0:
        parser.error("agent timeout must be positive")
    agent_argv = None
    if args.agent_argv:
        try:
            agent_argv = json.loads(args.agent_argv)
        except json.JSONDecodeError:
            parser.error("agent argv must be a JSON array of strings")
        if not isinstance(agent_argv, list) or not all(
            isinstance(part, str) and part for part in agent_argv
        ):
            parser.error("agent argv must be a JSON array of nonempty strings")
    report = run_selftest(
        seed=args.seed,
        arms=tuple(args.arms),
        levels=tuple(args.levels),
        max_attempts=args.attempts,
        history_mode=args.history,
        agent_argv=agent_argv,
        agent_timeout=args.agent_timeout,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    get_logger().info(f"local selftest report written to {output}")
    summary = report["summary"]
    if {"baseline", "tuned"} <= set(summary) and args.fail_on_regression:
        baseline = summary["baseline"]["overall"]["resolved_rate"] or 0.0
        tuned = summary["tuned"]["overall"]["resolved_rate"] or 0.0
        if tuned < baseline:
            get_logger().error("tuned policy regressed against baseline")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
