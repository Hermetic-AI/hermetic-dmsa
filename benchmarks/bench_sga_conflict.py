"""Benchmark 1：冲突反馈下的 prompt 优化 —— SGA vs TextGrad 式拼接基线。

验证 `analysis/02_semantic_gradient_aggregation.md` 的声称：
文本梯度冲突（"简洁 vs 详尽"）时，拼接+LLM 总结会震荡，SGA 的
聚类 + 冲突检测 + 条件分支应更稳、更快收敛。

设置：
- 两类用户段，偏好直接冲突：[quick] 问题要 ≤40 词，[deep-dive] 问题要 ≥300 词
- 评判 = 规则（word count），零 LLM judge 噪声
- baseline 臂：当轮反馈拼接 → LLM 归并为**单条无条件指令**（TextGrad 的
  ephemeral batch 梯度 + ``_check_and_reduce_gradients`` 语义，无记忆）
- SGA 臂：滚动窗口累积反馈 → SGAAggregator（OpenAI 嵌入 + LLMFusion）
  → 冲突分支 → LLM 改写（DMSA 的记忆持久化 + 聚合语义）

两臂反馈可见性不同是有意的：被测的正是"无记忆的批量梯度归约" vs
"有记忆的梯度聚合"这个设计差异本身。

指标：每轮各段满意率、满意率之差的 stdev（震荡）、极化轮数、
双臂都 ≥2/3 满意的首轮（收敛速度）、LLM 调用数。
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # common.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import (  # noqa: E402
    CHAT_MODEL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    LLMCaller,
    dump_results,
    word_count,
)

from dmsa.gradient import (  # noqa: E402
    LLMFusion,
    OpenAIEmbedder,
    SGAAggregator,
    StaticFusion,
    make_sample,
)

ITERATIONS = 6
QUICK_MAX_WORDS = 40
DEEP_MIN_WORDS = 300  # 足够高：折中/简洁指令下 deep 段必然失败，震荡才可见
CONVERGE_THRESHOLD = 2 / 3
FEEDBACK_WINDOW = 2  # SGA 臂滚动窗口（模拟 DMSA 的记忆累积）；baseline 臂只看当轮

QUESTIONS = {
    "quick": [
        "[quick] What is the capital of France?",
        "[quick] Who wrote Romeo and Juliet?",
        "[quick] What is the chemical symbol for gold?",
    ],
    "deep": [
        "[deep-dive] Explain how photosynthesis works.",
        "[deep-dive] Explain the causes of World War I.",
        "[deep-dive] Explain how neural networks learn.",
    ],
}

INITIAL_PROMPT = (
    "You are a helpful assistant. Answer every question in exactly 100 words — "
    "no more, no less."
)  # 折中 prompt：首轮两段同时失败，让两类冲突反馈同批进入聚合

_FEEDBACK = {
    "quick": [
        "The answer to a [quick] question was too long. Quick users want a short, direct reply.",
        "Too verbose for a [quick] question — keep it brief and to the point.",
        "Please be much more concise when the question is marked [quick].",
    ],
    "deep": [
        "The answer to a [deep-dive] question was too brief. Deep-dive users want in-depth coverage.",
        "Too shallow for a [deep-dive] question — give a thorough, detailed explanation.",
        "Please elaborate much more when the question is marked [deep-dive].",
    ],
}


def _satisfied(segment: str, answer: str) -> bool:
    """规则评判：quick ≤ 40 词，deep ≥ 120 词。"""
    words = word_count(answer)
    if segment == "quick":
        return words <= QUICK_MAX_WORDS
    return words >= DEEP_MIN_WORDS


def _run_round(caller: LLMCaller, prompt: str) -> dict[str, Any]:
    """用当前 system prompt 跑全部问题，返回满意率 + 反馈样本。"""
    per_segment: dict[str, list[bool]] = {"quick": [], "deep": []}
    samples = []
    for segment, questions in QUESTIONS.items():
        for i, question in enumerate(questions):
            answer = caller.chat(system=prompt, user=question, max_tokens=1000)
            ok = _satisfied(segment, answer)
            per_segment[segment].append(ok)
            if not ok:
                samples.append(
                    make_sample(
                        feedback_text=_FEEDBACK[segment][i],
                        source="AUTOMATED_TEST",
                        severity=0.8,
                        target_node_id="bench-prompt",
                    )
                )
    return {
        "sat_quick": sum(per_segment["quick"]) / len(QUESTIONS["quick"]),
        "sat_deep": sum(per_segment["deep"]) / len(QUESTIONS["deep"]),
        "samples": samples,
    }


def _baseline_rewrite(caller: LLMCaller, prompt: str, samples: list[Any]) -> str:
    """TextGrad 风格基线：把窗口内全部反馈归并成**一条**全局指令。

    忠实复现 TextGrad ``_check_and_reduce_gradients`` 的语义：拼接 +
    LLM 归约为单一指令（禁止条件分支）。失败模式 = 折中（双臂都不满意）
    或在两个极端间震荡。SGA 臂的卖点恰恰是条件分支能存活。
    """
    numbered = "\n".join(f"{i}. {s.feedback_text}" for i, s in enumerate(samples, 1))
    directive = caller.chat(
        system=(
            "You consolidate user feedback into rules for an assistant. "
            "Output ONLY the consolidated rule."
        ),
        user=(
            "Consolidate ALL the feedback below into ONE single global rule "
            "(at most 2 sentences) that applies to EVERY answer. The rule must "
            "be unconditional — no if/else, no per-case handling.\n\n"
            f"{numbered}"
        ),
    )
    return f"{INITIAL_PROMPT}\n{directive}"


def _sga_rewrite(
    caller: LLMCaller,
    aggregator: SGAAggregator,
    prompt: str,
    samples: list[Any],
) -> tuple[str, dict[str, Any]]:
    """SGA：聚类 + 融合 + 冲突分支 → LLM 改写 prompt。"""
    result = aggregator.aggregate(samples, feedback_id="bench")
    blocks = []
    for fused in result.fused_gradients:
        block = f"- cluster {fused.cluster_id}: {fused.fused_text}"
        if fused.branch_conditions:
            block += "\n  branches: " + " | ".join(fused.branch_conditions)
        blocks.append(block)
    new_prompt = caller.chat(
        system="You are an expert prompt engineer. Output ONLY the rewritten system prompt.",
        user=(
            "Current system prompt:\n"
            f'"""{prompt}"""\n\n'
            "Aggregated feedback gradients (conflicting clusters include "
            "conditional branches):\n"
            + "\n".join(blocks)
            + "\n\nRewrite the system prompt to address every gradient. "
            "If a gradient has conditions (if/else branches), KEEP that "
            "conditional structure in the rewritten prompt. "
            "Output ONLY the new system prompt."
        ),
    )
    info = {
        "cluster_count": len(result.clusters),
        "conflicts": sum(1 for f in result.fused_gradients if f.has_conflict),
        "branches": [list(f.branch_conditions) for f in result.fused_gradients],
    }
    return new_prompt, info


def _converged_iter(rows: list[dict[str, Any]]) -> int | None:
    """双臂都 ≥ 阈值并保持到末尾的首轮（1-based）。"""
    for i, row in enumerate(rows):
        if all(
            r["sat_quick"] >= CONVERGE_THRESHOLD and r["sat_deep"] >= CONVERGE_THRESHOLD
            for r in rows[i:]
        ):
            return i + 1
    return None


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """震荡与收敛指标。"""
    gaps = [r["sat_quick"] - r["sat_deep"] for r in rows]
    polarized = sum(
        1
        for r in rows
        if (r["sat_quick"] == 1.0 and r["sat_deep"] == 0.0)
        or (r["sat_quick"] == 0.0 and r["sat_deep"] == 1.0)
    )
    return {
        "gap_stdev": statistics.pstdev(gaps) if len(gaps) > 1 else 0.0,
        "mean_gap_abs": statistics.mean(abs(g) for g in gaps),
        "polarized_rounds": polarized,
        "converged_iter": _converged_iter(rows),
    }


def main() -> None:
    """跑双臂 benchmark 并落盘结果。

    两臂的反馈可见性不同，这是有意的、也正是被测的设计差异：
    - baseline 臂只看**当轮**反馈（TextGrad 的 ephemeral batch 梯度语义）
    - SGA 臂看**滚动窗口**的累积反馈（DMSA 的记忆持久化 + 聚合语义）
    """
    caller = LLMCaller()

    # ---- baseline 臂（TextGrad 式：当轮反馈拼接归并，无记忆） ----
    prompt = INITIAL_PROMPT
    baseline_rows: list[dict[str, Any]] = []
    for iteration in range(1, ITERATIONS + 1):
        round_result = _run_round(caller, prompt)
        samples = round_result.pop("samples")
        baseline_rows.append({"iter": iteration, "prompt": prompt, **round_result})
        print(
            f"[baseline] iter {iteration}: quick={round_result['sat_quick']:.0%} "
            f"deep={round_result['sat_deep']:.0%} feedbacks={len(samples)}",
            flush=True,
        )
        if samples:
            prompt = _baseline_rewrite(caller, prompt, samples)
    baseline_calls = caller.chat_calls

    # ---- SGA 臂 ----
    aggregator = SGAAggregator(
        embedder=OpenAIEmbedder(
            client=caller.client, model=EMBEDDING_MODEL, dim=EMBEDDING_DIM
        ),
        fusion=LLMFusion(
            client=caller.client, model=CHAT_MODEL, fallback=StaticFusion()
        ),
    )
    prompt = INITIAL_PROMPT
    history = []
    sga_rows: list[dict[str, Any]] = []
    sga_infos: list[dict[str, Any]] = []
    stable_rounds = 0
    for iteration in range(1, ITERATIONS + 1):
        round_result = _run_round(caller, prompt)
        samples = round_result.pop("samples")
        history.append(samples)
        sga_rows.append({"iter": iteration, "prompt": prompt, **round_result})
        print(
            f"[sga] iter {iteration}: quick={round_result['sat_quick']:.0%} "
            f"deep={round_result['sat_deep']:.0%} feedbacks={len(samples)}",
            flush=True,
        )
        # 连续 2 轮零失败 = 已稳定收敛（temperature=0 确定性），提前结束省时
        if not samples:
            stable_rounds += 1
            if stable_rounds >= 2:
                print(f"[sga] stable for 2 rounds, early stop at iter {iteration}", flush=True)
                break
        else:
            stable_rounds = 0
        window = [s for batch in history[-FEEDBACK_WINDOW:] for s in batch]
        if window:
            prompt, info = _sga_rewrite(caller, aggregator, prompt, window)
            print(
                f"[sga] iter {iteration} aggregate: clusters={info['cluster_count']} "
                f"conflicts={info['conflicts']} branches={info['branches']}",
                flush=True,
            )
            sga_infos.append({"iter": iteration, **info})
    sga_calls = caller.chat_calls - baseline_calls

    summary = {
        "baseline": {**_summarize(baseline_rows), "chat_calls": baseline_calls},
        "sga": {**_summarize(sga_rows), "chat_calls": sga_calls},
    }
    path = dump_results(
        "sga_conflict",
        {
            "config": {
                "iterations": ITERATIONS,
                "quick_max_words": QUICK_MAX_WORDS,
                "deep_min_words": DEEP_MIN_WORDS,
                "chat_model": CHAT_MODEL,
                "embedding_model": EMBEDDING_MODEL,
            },
            "baseline_rows": baseline_rows,
            "sga_rows": sga_rows,
            "sga_aggregation": sga_infos,
            "summary": summary,
        },
    )

    # ---- 打印摘要 ----
    print("\n=== SGA vs TextGrad-style concat baseline ===")
    print(f"{'iter':>4} | {'base quick':>10} {'base deep':>9} | {'sga quick':>9} {'sga deep':>8}")
    for b, s in zip(baseline_rows, sga_rows):
        print(
            f"{b['iter']:>4} | {b['sat_quick']:>10.0%} {b['sat_deep']:>9.0%} | "
            f"{s['sat_quick']:>9.0%} {s['sat_deep']:>8.0%}"
        )
    print("\nsummary:")
    for arm, s in summary.items():
        print(
            f"  {arm:>8}: gap_stdev={s['gap_stdev']:.3f} "
            f"polarized={s['polarized_rounds']}/{ITERATIONS} "
            f"converged_iter={s['converged_iter']} calls={s['chat_calls']}"
        )
    print(f"\nresults -> {path}")


if __name__ == "__main__":
    main()
