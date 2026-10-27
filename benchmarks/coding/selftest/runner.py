# ruff: noqa: RUF001
"""本地自出题闭环: 建仓 -> Add -> Search -> 打补丁 -> pytest 判定."""

from __future__ import annotations

import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.coding.selftest.cases import NOISE_COUNTS, NOISE_LEVELS, Case, build_cases
from benchmarks.coding.selftest.solver import solve, tests_pass
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage, estimate_tokens

BASELINE = SearchPolicy(
    candidate_limit=80,
    max_chars=12000,
    per_session_limit=4,
    redundancy_threshold=0.88,
    max_tokens=12000,
)
TUNED = SearchPolicy()
POLICIES = {"baseline": BASELINE, "tuned": TUNED}


def _write_repo(root: Path, files: dict[str, str]) -> None:
    """把合成仓库写入隔离临时目录."""
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _add_history(memory: CodingMemory, case: Case, scope: str, level: str) -> None:
    """按平台语义分批 Add: 相关会话与噪声会话使用不同 session/request."""
    for index, session in enumerate(case.sessions):
        messages = [SourceMessage(role, content) for role, content in session]
        memory.add(
            f"{case.case_id}-{level}-rel-{index}",
            scope,
            f"rel-{index}",
            messages,
        )
    # 平台历史里同一会话包含多轮消息, 每 5 条聚成一个 session 才能触发 per_session_limit。
    for index, text in enumerate(case.noise[: NOISE_COUNTS[level]]):
        memory.add(
            f"{case.case_id}-{level}-noise-{index}",
            scope,
            f"noise-{index // 5}",
            [SourceMessage("assistant", text)],
        )


def _run_once(case: Case, level: str, arm: str, max_attempts: int) -> dict[str, Any]:
    """执行一次完整闭环并返回可审计的逐项指标."""
    policy = POLICIES[arm]
    with tempfile.TemporaryDirectory(prefix="dmsa-selftest-") as directory:
        root = Path(directory)
        _write_repo(root, case.files)
        initially_failing = not tests_pass(root, case.test_file)
        memory = CodingMemory(":memory:", policy=policy)
        scope = f"selftest:{case.case_id}:{level}"
        try:
            _add_history(memory, case, scope, level)
            started = time.perf_counter()
            hits = memory.search(case.query, scope, 100)
            search_seconds = time.perf_counter() - started
            rows = memory.store.rows(scope)
        finally:
            memory.close()
        gold_ids = {str(row["id"]) for row in rows if case.gold_text in str(row["text"])}
        hit_ids = [hit.id for hit in hits]
        gold_rank = next(
            (rank for rank, hit_id in enumerate(hit_ids, 1) if hit_id in gold_ids), None
        )
        solve_started = time.perf_counter()
        result = solve([hit.content for hit in hits], root, case.test_file, max_attempts)
        solve_seconds = time.perf_counter() - solve_started
    return {
        "case_id": case.case_id,
        "kind": case.kind,
        "level": level,
        "arm": arm,
        "noise_messages": NOISE_COUNTS[level],
        "hits": len(hits),
        "gold_rank": gold_rank,
        "recall": bool(gold_ids) and gold_rank is not None,
        "evidence_chars": sum(len(hit.content) for hit in hits),
        "evidence_tokens": sum(estimate_tokens(hit.content) for hit in hits),
        "truncated_hits": sum(1 for hit in hits if hit.content.endswith("[truncated]")),
        "resolved": result.resolved,
        "attempts": result.attempts,
        "failed_attempts": len(result.failures),
        "wrong_path_attempts": sum(1 for path in result.failures if path != case.target_path),
        "applied_rank": result.applied_rank,
        "initially_failing": initially_failing,
        "search_ms": round(search_seconds * 1000, 3),
        "solve_ms": round(solve_seconds * 1000, 3),
    }


def _mean(values: list[float]) -> float | None:
    """返回均值; 空集合保持 null, 不伪造 0."""
    return round(sum(values) / len(values), 4) if values else None


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总一组运行; 只报告真实执行到的指标."""
    return {
        "runs": len(rows),
        "resolved_rate": _mean([float(row["resolved"]) for row in rows]),
        "evidence_recall": _mean([float(row["recall"]) for row in rows]),
        "mean_hits": _mean([float(row["hits"]) for row in rows]),
        "mean_evidence_tokens": _mean([float(row["evidence_tokens"]) for row in rows]),
        "runs_with_truncation": sum(1 for row in rows if row["truncated_hits"]),
        "failed_attempts": sum(int(row["failed_attempts"]) for row in rows),
        "wrong_path_attempts": sum(int(row["wrong_path_attempts"]) for row in rows),
        "mean_search_ms": _mean([float(row["search_ms"]) for row in rows]),
    }


def run_selftest(
    seed: int = 17,
    arms: tuple[str, ...] = ("baseline", "tuned"),
    levels: tuple[str, ...] = NOISE_LEVELS,
    max_attempts: int = 10,
) -> dict[str, Any]:
    """运行全部合成任务并返回不改写官方分数的本地报告."""
    if any(arm not in POLICIES for arm in arms):
        raise ValueError("unknown arm")
    if not levels or any(level not in NOISE_LEVELS for level in levels):
        raise ValueError("unknown noise level")
    cases = build_cases(seed)
    runs = [
        _run_once(case, level, arm, max_attempts)
        for level in levels
        for case in cases
        for arm in arms
    ]
    summary = {
        arm: {
            "overall": _summarize([row for row in runs if row["arm"] == arm]),
            "by_level": {
                level: _summarize(
                    [row for row in runs if row["arm"] == arm and row["level"] == level]
                )
                for level in levels
            },
        }
        for arm in arms
    }
    return {
        "schema_version": 1,
        "evaluation_type": "local_self_generated_e2e_not_official",
        "official_score": None,
        "seed": seed,
        "max_attempts": max_attempts,
        "tasks": [case.case_id for case in cases],
        "noise_counts": {level: NOISE_COUNTS[level] for level in levels},
        "policies": {arm: asdict(POLICIES[arm]) for arm in arms},
        "summary": summary,
        "runs": runs,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """把本地报告渲染成简短、可审计的 Markdown."""
    lines = [
        "# Coding Memory local self-generated evaluation",
        "",
        "> 本地合成任务与本地 pytest 判定，不是官方成绩，`official_score` 固定为 null。",
        "",
        f"Seed: `{report['seed']}`; 每任务最多尝试 `{report['max_attempts']}` 个补丁。",
        "",
        "| Arm | Runs | Resolved | Recall | Mean hits | Mean tokens | Failed attempts |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, values in report["summary"].items():
        overall = values["overall"]
        lines.append(
            f"| {arm} | {overall['runs']} | {overall['resolved_rate']} | "
            f"{overall['evidence_recall']} | {overall['mean_hits']} | "
            f"{overall['mean_evidence_tokens']} | {overall['failed_attempts']} |"
        )
    lines.extend(
        [
            "",
            "| Arm | Noise | Resolved | Recall | Mean hits | Mean tokens |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for arm, values in report["summary"].items():
        for level, level_values in values["by_level"].items():
            lines.append(
                f"| {arm} | {level} | {level_values['resolved_rate']} | "
                f"{level_values['evidence_recall']} | {level_values['mean_hits']} | "
                f"{level_values['mean_evidence_tokens']} |"
            )
    return "\n".join(lines) + "\n"
