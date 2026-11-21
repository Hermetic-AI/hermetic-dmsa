"""Lineage 通道的配对检索回放: 在含经验更新的语料上比较默认策略与 lineage 策略.

与 ``lineage_ablation`` 的分工: 那里只测"取代标记能否送达 Agent"; 这里用项目
自带的 ``run_replay`` + ``offline-rsi-gate-v2`` 做配对检索, 回答一个更保守的问题:

    lineage 通道在"不改变检索质量"的前提下, 是否额外送达了取代信息?

因此报告两组指标, 不混为一谈:
  * 检索质量(recall / NDCG / token): 由冻结语料上的配对回放给出, 门禁只认这些;
  * 取代信息送达率(supersedes marker): 单独统计, 门禁目前不认这一维度。

如果 recall/NDCG 没有严格改善, gate 会拒绝 lineage 自动激活。这不是失败, 而是
本模块的预期结论: lineage 的价值是透明度, 不是排序质量; 它应由调用方显式打开,
而不是靠只认 recall/NDCG 的门禁自动上位。

用法::

    python -m benchmarks.coding.lineage_replay [--out results/lineage-replay.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.coding.dataset import load_manifest, task_objects
from dmsa.gradient import ReplayCase, RetrievalPolicy, gate, run_replay
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME

DEFAULT_MANIFEST = Path("benchmarks/coding/fixtures/mini.json")
TOP_K = 5
BASELINE = SearchPolicy()
CANDIDATE = SearchPolicy(lineage_window=1)


def _turns(task_id: str) -> tuple[tuple[SourceMessage, ...], tuple[SourceMessage, ...]]:
    """构造同一会话的两轮经验更新: 早期方案被最终修复取代。"""
    early = (
        SourceMessage("user", f"{task_id}: 早期实现有问题, 需要修复。"),
        SourceMessage(
            "assistant",
            f"早期方案: 先用简化实现。\n"
            f"```python path=src/{task_id}.py\n"
            f"def handle():\n    return SIMPLE_VALUE\n"
            f"```\n验证: pytest tests/test_{task_id}.py -q: 1 passed",
        ),
    )
    final = (
        SourceMessage("assistant", "复核: 早期实现在并发下失效。"),
        SourceMessage(
            "assistant",
            f"最终方案: 加入版本比较。\n"
            f"```python path=src/{task_id}.py\n"
            f"def handle():\n    return check_version()\n"
            f"```\n验证: pytest tests/test_{task_id}.py -q: 1 passed",
        ),
    )
    return early, final


def _build_cases(
    memory: CodingMemory, tasks: tuple[Any, ...]
) -> tuple[tuple[ReplayCase, ...], list[dict[str, str]]]:
    """写入两轮语料, 返回回放用例与每个用例的 query/scope 元数据。"""
    cases: list[ReplayCase] = []
    meta: list[dict[str, str]] = []
    for task in tasks:
        scope = f"lineage-{task.id}"
        early, final = _turns(task.id)
        memory.add(f"{task.id}-r1", scope, task.id, list(early))
        memory.add(f"{task.id}-r2", scope, task.id, list(final))
        rows = [row for row in memory.store.rows(scope) if row["role"] == "episode"]
        active = tuple(str(row["id"]) for row in rows if str(row["outcome"]) != SUPERSEDED_OUTCOME)
        if len(rows) != 2 or len(active) != 1:
            # 语料构造失败时跳过, 不把不完整样本喂给门禁。
            continue
        cases.append(ReplayCase(task.id, scope, task.query, active, "two-round-update"))
        meta.append({"case_id": task.id, "user_id": scope, "query": task.query})
    return tuple(cases), meta


def _visibility(memory: CodingMemory, meta: list[dict[str, str]]) -> dict[str, float]:
    """统计两臂里带取代标记的命中占比; 这是透明度指标, 不进入门禁。"""
    result: dict[str, float] = {}
    for name, policy in (("default", BASELINE), ("lineage", CANDIDATE)):
        marked = 0
        for item in meta:
            hits = memory.search(item["query"], item["user_id"], TOP_K, policy)
            marked += any("[supersedes " in hit.content for hit in hits)
        result[name] = marked / len(meta) if meta else 0.0
    return result


def run(manifest_path: Path) -> dict[str, Any]:
    """在临时库上执行配对回放, 返回机器可读的结论。"""
    tasks = task_objects(load_manifest(manifest_path))
    with tempfile.TemporaryDirectory() as raw:
        memory = CodingMemory(Path(raw) / "lineage-replay.sqlite")
        try:
            cases, meta = _build_cases(memory, tasks)
            if not cases:
                return {"error": "no valid replay case could be constructed"}
            report = run_replay(
                memory,
                RetrievalPolicy(BASELINE),
                RetrievalPolicy(CANDIDATE),
                cases,
                top_k=TOP_K,
                max_chars=BASELINE.max_chars,
                max_tokens=BASELINE.max_tokens,
                development_only=True,
            )
            visibility = _visibility(memory, meta)
        finally:
            memory.close()
    return {
        "schema_version": 1,
        "evaluation_type": "synthetic_proxy_not_official",
        "official_score": None,
        "cases": len(cases),
        "gate_version": report.gate_version,
        "gate_passed": gate(report),
        "retrieval_quality": {
            "default": {
                "mean_recall": report.mean_recall_before,
                "mean_ndcg": report.mean_ndcg_before,
            },
            "lineage": {
                "mean_recall": report.mean_recall_after,
                "mean_ndcg": report.mean_ndcg_after,
            },
            "report_hash": report.report_hash,
        },
        "supersession_visibility": visibility,
        "verdict": (
            "lineage 未改善 recall/NDCG, 门禁按设计拒绝自动激活; "
            "其价值在取代信息送达率, 应由调用方显式开启"
            if not gate(report)
            else "lineage 在配对回放中严格改善检索质量且无退化, 门禁接受"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """打印结论, 可选写入 JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    payload = json.dumps(run(args.manifest), ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
