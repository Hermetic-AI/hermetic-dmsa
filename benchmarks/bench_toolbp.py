"""Benchmark 2：工具格式错误 —— ToolBP（改 schema）vs prompt-only（只改提示词）。

验证 `analysis/03_tool_use_backpropagation.md` 的声称：
工具调用格式错误的"梯度"应流向 JSON Schema 本身，而不是堆进 prompt。

设置：
- 工具 get_weather(city, date)，date 必须严格匹配 YYYY-MM-DD
- 查询故意带相对日期（明天 / day after tomorrow），诱导模型输出非法 date
- prompt-only 臂：失败信息逐轮堆进 system prompt（Reflexion 风格）
- ToolBP 臂：ToolCallCritic → ToolBPEngine.project 直接改 schema（version+1）

指标：每轮格式错误率（6 个查询中失败的比例）、schema version、LLM 调用数。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # common.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import LLMCaller, dump_results  # noqa: E402

from dmsa.toolbp import ToolBPEngine, ToolCallCritic, make_schema_node  # noqa: E402
from dmsa.types import ToolCallRecord  # noqa: E402

ITERATIONS = 5
TODAY = "2026-08-10"

QUERIES = [
    "北京明天的天气怎么样？",
    "What is the weather in Shanghai the day after tomorrow?",
    "深圳后天会下雨吗？",
    "Weather in New York tomorrow, please.",
    "杭州明天需要带伞吗？",
    "Check the weather in London for the day after tomorrow.",
]

INITIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "name": "get_weather",
    "description": "Get the weather for a city on a given date.",
    "properties": {
        "city": {"type": "string", "description": "the city"},
        "date": {"type": "string", "description": "the date"},
    },
    "required": ["city", "date"],
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _simulate_tool(args: Any) -> str | None:
    """模拟工具校验；返回错误消息，None = 成功。"""
    if not isinstance(args, dict):
        return "args is not a JSON object (schema validation failed)"
    if not args.get("city"):
        return "missing required field: city"
    if "date" not in args:
        return "missing required field: date"
    if not _DATE_RE.match(str(args["date"])):
        return f"date format invalid: expected YYYY-MM-DD, got {args['date']!r}"
    return None


def _ask_agent(caller: LLMCaller, system: str, query: str) -> Any:
    """让 LLM 输出工具调用参数（JSON）。"""
    raw = caller.chat(system=system, user=query, max_tokens=300)
    match = _JSON_RE.search(raw)
    if not match:
        return raw  # 不是 JSON → 模拟工具会报 schema validation failed
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return raw


def _run_arm(
    caller: LLMCaller,
    *,
    arm: str,
    use_toolbp: bool,
) -> list[dict[str, Any]]:
    """跑一条臂，返回每轮结果。"""
    engine = ToolBPEngine()
    critic = ToolCallCritic()
    schema_node = make_schema_node(
        schema_id="bench-weather", name="get_weather", schema_dict=INITIAL_SCHEMA
    )
    past_errors: list[str] = []
    rows: list[dict[str, Any]] = []

    for iteration in range(1, ITERATIONS + 1):
        if use_toolbp:
            schema_block = json.dumps(schema_node.schema_dict, ensure_ascii=False)
            system = (
                "You are a weather assistant. Call the get_weather tool by "
                "outputting ONLY a JSON object with the call arguments.\n"
                f"Tool JSON Schema:\n{schema_block}\n"
                f"Today's date is {TODAY}."
            )
        else:
            system = (
                "You are a weather assistant. Call the get_weather tool by "
                "outputting ONLY a JSON object with the call arguments.\n"
                f"Tool JSON Schema:\n{json.dumps(INITIAL_SCHEMA, ensure_ascii=False)}\n"
                f"Today's date is {TODAY}."
            )
            if past_errors:
                joined = "\n".join(f"- {e}" for e in past_errors[-10:])
                system += f"\nPrevious tool call errors to avoid:\n{joined}"

        failures = 0
        for query in QUERIES:
            args = _ask_agent(caller, system, query)
            error = _simulate_tool(args)
            if error is None:
                continue
            failures += 1
            past_errors.append(error)
            if use_toolbp:
                record = ToolCallRecord(
                    call_id=f"iter{iteration}-{query[:12]}",
                    tool_name="get_weather",
                    args=args if isinstance(args, dict) else {"raw": str(args)[:100]},
                    error=error,
                )
                feedback = critic.critique(record)
                schema_node = engine.project(schema=schema_node, feedback=feedback)

        rows.append(
            {
                "iter": iteration,
                "error_rate": failures / len(QUERIES),
                "failures": failures,
                "schema_version": schema_node.version,
            }
        )
    return rows


def main() -> None:
    """跑双臂 benchmark 并落盘结果。"""
    caller = LLMCaller()

    prompt_only_rows = _run_arm(caller, arm="prompt-only", use_toolbp=False)
    prompt_only_calls = caller.chat_calls

    toolbp_rows = _run_arm(caller, arm="toolbp", use_toolbp=True)
    toolbp_calls = caller.chat_calls - prompt_only_calls

    summary = {
        "prompt_only": {
            "mean_error_rate": sum(r["error_rate"] for r in prompt_only_rows) / ITERATIONS,
            "final_error_rate": prompt_only_rows[-1]["error_rate"],
            "chat_calls": prompt_only_calls,
        },
        "toolbp": {
            "mean_error_rate": sum(r["error_rate"] for r in toolbp_rows) / ITERATIONS,
            "final_error_rate": toolbp_rows[-1]["error_rate"],
            "final_schema_version": toolbp_rows[-1]["schema_version"],
            "chat_calls": toolbp_calls,
        },
    }
    reduction = (
        1 - summary["toolbp"]["mean_error_rate"] / summary["prompt_only"]["mean_error_rate"]
        if summary["prompt_only"]["mean_error_rate"] > 0
        else 0.0
    )
    summary["error_rate_reduction"] = reduction

    path = dump_results(
        "toolbp_format",
        {
            "config": {"iterations": ITERATIONS, "queries": QUERIES, "today": TODAY},
            "prompt_only_rows": prompt_only_rows,
            "toolbp_rows": toolbp_rows,
            "summary": summary,
        },
    )

    print("\n=== ToolBP (schema update) vs prompt-only ===")
    print(f"{'iter':>4} | {'prompt-only err':>15} | {'toolbp err':>10} {'schema v':>8}")
    for p, t in zip(prompt_only_rows, toolbp_rows):
        print(
            f"{p['iter']:>4} | {p['error_rate']:>15.0%} | "
            f"{t['error_rate']:>10.0%} {t['schema_version']:>8}"
        )
    print(
        f"\nmean error rate: prompt-only={summary['prompt_only']['mean_error_rate']:.0%} "
        f"toolbp={summary['toolbp']['mean_error_rate']:.0%} "
        f"(reduction {reduction:.0%})"
    )
    print(f"results -> {path}")


if __name__ == "__main__":
    main()
