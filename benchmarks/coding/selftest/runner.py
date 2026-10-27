# ruff: noqa: RUF001
"""本地自出题闭环: 建仓 -> Add -> Search -> 打补丁 -> pytest 判定."""

from __future__ import annotations

import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.coding.selftest.agent import run_agent
from benchmarks.coding.selftest.analogy import Analogy, build_analogies
from benchmarks.coding.selftest.cases import NOISE_COUNTS, NOISE_LEVELS, Case, build_cases
from benchmarks.coding.selftest.solver import (
    SolveResult,
    solve,
    solve_by_analogy,
    tests_pass,
)
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage, estimate_tokens

BASELINE = SearchPolicy(
    candidate_limit=80,
    max_chars=12000,
    per_session_limit=4,
    redundancy_threshold=0.88,
    max_tokens=12000,
    evidence_limit=1000,
    score_floor_ratio=0.0,
    per_path_limit=1000,
)
TUNED = SearchPolicy()
POLICIES = {"baseline": BASELINE, "tuned": TUNED}
# case: 历史含标准答案, 只验证检索上限; analogy: 历史是同族异标识符的已解任务;
# none: 完全无历史, 作为 no-memory 对照下限。
HISTORY_MODES = ("case", "analogy", "none")


def _write_repo(root: Path, files: dict[str, str]) -> None:
    """把合成仓库写入隔离临时目录."""
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _add_history(
    memory: CodingMemory,
    scope: str,
    level: str,
    sessions: tuple[tuple[tuple[str, str], ...], ...],
    noise: tuple[str, ...],
) -> None:
    """按平台语义分批 Add: 同一段对话共用一个 session, 噪声各自独立 session."""
    for index, session in enumerate(sessions):
        messages = [SourceMessage(role, content) for role, content in session]
        memory.add(
            f"{scope}-{level}-rel-{index}",
            scope,
            "rel",
            messages,
        )
    # 平台历史里同一会话包含多轮消息, 每 5 条聚成一个 session 才能触发 per_session_limit。
    for index, text in enumerate(noise[: NOISE_COUNTS[level]]):
        memory.add(
            f"{scope}-{level}-noise-{index}",
            scope,
            f"noise-{index // 5}",
            [SourceMessage("assistant", text)],
        )


def _mean(values: list[float]) -> float | None:
    """返回均值; 空集合保持 null, 不伪造 0."""
    return round(sum(values) / len(values), 4) if values else None


def _solve_with_agent(
    agent_argv: list[str],
    agent_timeout: float,
    case: Case,
    root: Path,
    evidence: list[str],
) -> tuple[SolveResult, dict[str, Any]]:
    """调用外部 Agent 改仓库, 再用本地 pytest 独立判分."""
    run = run_agent(
        agent_argv,
        {
            "query": case.query,
            "repo_path": str(root),
            "test_file": case.test_file,
            "target_path": case.target_path,
            "evidence": evidence,
        },
        agent_timeout,
    )
    resolved = tests_pass(root, case.test_file)
    result = SolveResult(
        resolved=resolved,
        attempts=1,
        localized_path=run.claimed_path,
        applied_rank=None,
        failures=(),
    )
    return result, {
        "agent_return_code": run.return_code,
        "agent_claimed_resolved": run.claimed_resolved,
        "agent_latency_ms": round(run.latency_seconds * 1000, 3),
    }


def _run_once(
    case: Case,
    level: str,
    arm: str,
    max_attempts: int,
    history_mode: str = "case",
    analogy: Analogy | None = None,
    agent_argv: list[str] | None = None,
    agent_timeout: float = 600.0,
) -> dict[str, Any]:
    """执行一次完整闭环并返回可审计的逐项指标."""
    policy = POLICIES[arm]
    with tempfile.TemporaryDirectory(prefix="dmsa-selftest-") as directory:
        root = Path(directory)
        _write_repo(root, case.files)
        initially_failing = not tests_pass(root, case.test_file)
        memory = CodingMemory(":memory:", policy=policy)
        scope = f"selftest:{case.case_id}:{level}"
        try:
            if history_mode == "case":
                _add_history(memory, scope, level, case.sessions, case.noise)
            elif history_mode == "analogy":
                if analogy is None:
                    raise ValueError("analogy history requires an analogy case")
                _add_history(memory, scope, level, analogy.sessions, analogy.noise)
            started = time.perf_counter()
            hits = memory.search(case.query, scope, 100)
            search_seconds = time.perf_counter() - started
            rows = memory.store.rows(scope)
        finally:
            memory.close()
        evidence = [hit.content for hit in hits]
        gold_ids = {str(row["id"]) for row in rows if case.gold_text in str(row["text"])}
        hit_ids = [hit.id for hit in hits]
        gold_rank = next(
            (rank for rank, hit_id in enumerate(hit_ids, 1) if hit_id in gold_ids), None
        )
        answer_present = any(case.gold_text in content for content in evidence)
        analogy_present = (
            analogy is not None
            and any(analogy.gold_text in content for content in evidence)
        )
        solve_started = time.perf_counter()
        extra: dict[str, Any] = {}
        if agent_argv:
            result, extra = _solve_with_agent(
                agent_argv, agent_timeout, case, root, evidence
            )
        elif history_mode == "analogy" and analogy is not None:
            result = solve_by_analogy(
                evidence, root, case.test_file, analogy.substitutions, max_attempts
            )
        else:
            result = solve(evidence, root, case.test_file, max_attempts)
        solve_seconds = time.perf_counter() - solve_started
    return {
        "case_id": case.case_id,
        "kind": case.kind,
        "level": level,
        "arm": arm,
        "history_mode": history_mode,
        "noise_messages": NOISE_COUNTS[level],
        "hits": len(hits),
        "gold_rank": gold_rank,
        "recall": bool(gold_ids) and gold_rank is not None,
        "answer_present": answer_present,
        "analogy_present": analogy_present,
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
        **extra,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总一组运行; 只报告真实执行到的指标."""
    return {
        "runs": len(rows),
        "resolved_rate": _mean([float(row["resolved"]) for row in rows]),
        "evidence_recall": _mean([float(row["recall"]) for row in rows]),
        "answer_present_rate": _mean([float(row["answer_present"]) for row in rows]),
        "analogy_present_rate": _mean(
            [
                float(row["analogy_present"])
                for row in rows
                if row["analogy_present"] is not None
            ]
        ),
        "mean_hits": _mean([float(row["hits"]) for row in rows]),
        "mean_evidence_tokens": _mean([float(row["evidence_tokens"]) for row in rows]),
        "runs_with_truncation": sum(1 for row in rows if row["truncated_hits"]),
        "failed_attempts": sum(int(row["failed_attempts"]) for row in rows),
        "wrong_path_attempts": sum(int(row["wrong_path_attempts"]) for row in rows),
        "mean_search_ms": _mean([float(row["search_ms"]) for row in rows]),
        "mean_solve_ms": _mean([float(row["solve_ms"]) for row in rows]),
    }


def run_selftest(
    seed: int = 17,
    arms: tuple[str, ...] = ("baseline", "tuned"),
    levels: tuple[str, ...] = NOISE_LEVELS,
    max_attempts: int = 10,
    history_mode: str = "case",
    agent_argv: list[str] | None = None,
    agent_timeout: float = 600.0,
) -> dict[str, Any]:
    """运行全部合成任务并返回不改写官方分数的本地报告."""
    if any(arm not in POLICIES for arm in arms):
        raise ValueError("unknown arm")
    if not levels or any(level not in NOISE_LEVELS for level in levels):
        raise ValueError("unknown noise level")
    if history_mode not in HISTORY_MODES:
        raise ValueError("unknown history mode")
    cases = build_cases(seed)
    analogies = build_analogies(seed, cases)
    runs = [
        _run_once(
            case,
            level,
            arm,
            max_attempts,
            history_mode,
            analogies.get(case.case_id),
            agent_argv,
            agent_timeout,
        )
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
        "schema_version": 2,
        "evaluation_type": "local_self_generated_e2e_not_official",
        "official_score": None,
        "seed": seed,
        "max_attempts": max_attempts,
        "history_mode": history_mode,
        "agent_driven": agent_argv is not None,
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
        f"历史模式: `{report['history_mode']}`; 外部 Agent 驱动: `{report['agent_driven']}`。",
        "",
        "| Arm | Runs | Resolved | Answer present | Analogy present | Mean hits | Mean tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, values in report["summary"].items():
        overall = values["overall"]
        lines.append(
            f"| {arm} | {overall['runs']} | {overall['resolved_rate']} | "
            f"{overall['answer_present_rate']} | {overall['analogy_present_rate']} | "
            f"{overall['mean_hits']} | {overall['mean_evidence_tokens']} |"
        )
    lines.extend(
        [
            "",
            "| Arm | Noise | Resolved | Answer present | Analogy present | Mean hits |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for arm, values in report["summary"].items():
        for level, level_values in values["by_level"].items():
            lines.append(
                f"| {arm} | {level} | {level_values['resolved_rate']} | "
                f"{level_values['answer_present_rate']} | "
                f"{level_values['analogy_present_rate']} | {level_values['mean_hits']} |"
            )
    return "\n".join(lines) + "\n"
