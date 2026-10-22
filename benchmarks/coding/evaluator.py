"""离线可复现记忆检索基准与安全的 Agent 适配器."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.coding.adapters import AgentResult
from benchmarks.coding.dataset import LEVEL_COUNTS, LEVELS, ndcg, task_objects
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.models import Embedder
from dmsa.memory.coding.vector_index import VectorIndex

ARMS = ("no_memory", "sparse", "hybrid", "full")


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总完整子集的检索与可选 Agent 指标."""
    agent_rows = [row for row in rows if row["agent_result"] is not None]
    return {
        "task_count": len(rows),
        "evidence_recall": sum(row["evidence_recall"] for row in rows) / len(rows)
        if rows
        else None,
        "ndcg": sum(row["ndcg"] for row in rows) / len(rows) if rows else None,
        "misleading_memory_rate": sum(row["misleading_memory"] for row in rows) / len(rows)
        if rows
        else None,
        "agent_reported_resolved_rate": sum(row["agent_result"]["resolved"] for row in agent_rows)
        / len(agent_rows)
        if agent_rows
        else None,
        "agent_reported_test_pass_rate": sum(
            row["agent_result"]["test_passed"] for row in agent_rows
        )
        / len(agent_rows)
        if agent_rows
        else None,
        "localization_accuracy": sum(row["localized"] for row in agent_rows) / len(agent_rows)
        if agent_rows
        else None,
    }


def evaluate_fixture(
    manifest: dict[str, Any],
    *,
    arms: list[str] | None = None,
    seed: int = 0,
    agent: Callable[[dict[str, Any]], AgentResult] | None = None,
    agent_name: str | None = None,
    agent_config_sha256: str | None = None,
    embedder: Embedder | None = None,
    embedding_config_sha256: str | None = None,
    vector_index_factory: Callable[[], VectorIndex] | None = None,
    vector_backend: str = "sqlite",
    vector_config_sha256: str | None = None,
    max_agent_tokens: int | None = 20_000,
    max_cost_usd: float | None = None,
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    """运行四档合成噪声; 不把检索指标冒充解题/官方成绩."""
    selected_arms = arms or list(ARMS)
    if any(arm not in ARMS for arm in selected_arms):
        raise ValueError("unknown benchmark arm")
    if len(set(selected_arms)) != len(selected_arms):
        raise ValueError("benchmark arms must be unique")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if any(arm in {"hybrid", "full"} for arm in selected_arms) and embedder is None:
        raise ValueError("hybrid and full arms require an explicitly configured embedder")
    tasks = task_objects(manifest)
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    config_hash = hashlib.sha256(
        json.dumps(
            {
                "manifest": manifest_hash,
                "seed": seed,
                "arms": selected_arms,
                "agent": agent_name,
                "agent_config": agent_config_sha256,
                "embedding": embedding_config_sha256,
                "embedder": getattr(embedder, "fingerprint", getattr(embedder, "name", None)),
                "dimension": getattr(embedder, "dimension", None),
                "vector_backend": vector_backend,
                "vector_config": vector_config_sha256,
                "max_agent_tokens": max_agent_tokens,
                "max_cost_usd": max_cost_usd,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    cached: dict[str, dict[str, Any]] = {}
    if checkpoint is not None and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state.get("config_sha256") != config_hash:
            raise ValueError("checkpoint configuration does not match this run")
        cached = state.get("results", {})
    level_results: dict[str, list[dict[str, Any]]] = {level: [] for level in LEVELS}
    for level in LEVELS:
        for task in tasks:
            vector_index = vector_index_factory() if vector_index_factory is not None else None
            memory = CodingMemory(":memory:", embedder=embedder, vector_index=vector_index)
            count = manifest.get("noise_counts", LEVEL_COUNTS)[level]
            selected = sorted(
                task.distractors,
                key=lambda row: hashlib.sha256(
                    f"{seed}:{task.id}:{row['text']}".encode()
                ).hexdigest(),
            )[:count]
            scope = f"bench-{config_hash[:12]}-{level}-{task.id}"
            messages = [SourceMessage("assistant", task.evidence)] + [
                SourceMessage("tool", row["text"]) for row in selected
            ]
            memory.add(f"{task.id}-{level}", scope, f"{task.id}-{level}", messages)
            all_chunk_ids = tuple(str(row["id"]) for row in memory.store.rows(scope))
            gold_ids = {
                str(row["id"])
                for row in memory.store.rows_by_ids(scope, all_chunk_ids)
                if int(row["message_index"]) == 0
            }
            for arm in selected_arms:
                result_key = f"{level}/{task.id}/{arm}"
                if result_key in cached:
                    level_results[level].append(cached[result_key])
                    continue
                if arm == "no_memory":
                    hits = []
                else:
                    policy = SearchPolicy(
                        dense_weight=0 if arm == "sparse" else 1,
                        experience_weight=1 if arm == "full" else 0,
                    )
                    hits = memory.search(task.query, scope, 5, policy)
                evidence_ids = [hit.id for hit in hits]
                hit_classes = ["gold" if hit.id in gold_ids else "noise" for hit in hits]
                agent_result: AgentResult | None = None
                if agent is not None:
                    agent_payload: dict[str, Any] = {
                        "task": {
                            "id": task.id,
                            "kind": task.kind,
                            "query": task.query,
                        },
                        "arm": arm,
                        "noise_level": level,
                        "evidence": [hit.content for hit in hits],
                    }
                    agent_result = agent(agent_payload)
                    if (
                        max_agent_tokens is not None
                        and agent_result.token_count is not None
                        and agent_result.token_count > max_agent_tokens
                    ):
                        raise ValueError("agent result exceeds the configured token budget")
                    if (
                        max_cost_usd is not None
                        and agent_result.cost_usd is not None
                        and agent_result.cost_usd > max_cost_usd
                    ):
                        raise ValueError("agent result exceeds the configured cost budget")
                result_row: dict[str, Any] = {
                    "task_id": task.id,
                    "kind": task.kind,
                    "arm": arm,
                    "evidence_ids": evidence_ids,
                    "evidence_classes": hit_classes,
                    "evidence_recall": float(any(value == "gold" for value in hit_classes)),
                    "ndcg": ndcg(hit_classes, {"gold"}),
                    "misleading_memory": float(bool(hit_classes) and "gold" not in hit_classes),
                    "agent_result": asdict(agent_result) if agent_result is not None else None,
                    "localized": (agent_result.localized_path == task.target_path)
                    if agent_result is not None
                    else None,
                }
                level_results[level].append(result_row)
                if checkpoint is not None:
                    cached[result_key] = result_row
                    checkpoint.parent.mkdir(parents=True, exist_ok=True)
                    temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
                    temporary.write_text(
                        json.dumps(
                            {
                                "config_sha256": config_hash,
                                "results": cached,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    temporary.replace(checkpoint)
            memory.delete_scope(scope)
            memory.close()
    all_rows = [row for rows in level_results.values() for row in rows]
    task_rows = [row for row in all_rows if row["agent_result"] is not None]
    metrics = {
        "agent_reported_resolved_rate": sum(row["agent_result"]["resolved"] for row in task_rows)
        / len(task_rows)
        if task_rows
        else None,
        "agent_reported_test_pass_rate": sum(
            row["agent_result"]["test_passed"] for row in task_rows
        )
        / len(task_rows)
        if task_rows
        else None,
        "localization_accuracy": sum(row["localized"] for row in task_rows) / len(task_rows)
        if task_rows
        else None,
        "evidence_recall": sum(row["evidence_recall"] for row in all_rows) / len(all_rows),
        "ndcg": sum(row["ndcg"] for row in all_rows) / len(all_rows),
        "misleading_memory_rate": sum(row["misleading_memory"] for row in all_rows) / len(all_rows),
        "latency_seconds": sum(row["agent_result"]["latency_seconds"] for row in task_rows)
        / len(task_rows)
        if task_rows
        else None,
        "mean_tokens": sum(
            row["agent_result"]["token_count"]
            for row in task_rows
            if row["agent_result"]["token_count"] is not None
        )
        / sum(row["agent_result"]["token_count"] is not None for row in task_rows)
        if any(row["agent_result"]["token_count"] is not None for row in task_rows)
        else None,
        "total_cost_usd": sum(
            row["agent_result"]["cost_usd"]
            for row in task_rows
            if row["agent_result"]["cost_usd"] is not None
        )
        if any(row["agent_result"]["cost_usd"] is not None for row in task_rows)
        else None,
    }
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    arm_slices = {
        arm: {
            level: _summarize([row for row in level_results[level] if row["arm"] == arm])
            for level in LEVELS
        }
        for arm in selected_arms
    }
    kind_slices = {
        kind: _summarize([row for row in all_rows if row["kind"] == kind])
        for kind in ("bug", "feature")
    }
    degradation = {
        arm: arm_slices[arm]["low"]["evidence_recall"] - arm_slices[arm]["high"]["evidence_recall"]
        if arm_slices[arm]["low"]["evidence_recall"] is not None
        and arm_slices[arm]["high"]["evidence_recall"] is not None
        else None
        for arm in selected_arms
    }
    return {
        "schema_version": 1,
        "evaluation_type": "synthetic_proxy_not_official",
        "official_score": None,
        "run_metadata": {
            "seed": seed,
            "manifest_sha256": manifest_hash,
            "config_sha256": config_hash,
            "git_commit": revision,
            "arms": selected_arms,
            "agent": agent_name,
            "agent_config_sha256": agent_config_sha256,
            "embedding_config_sha256": embedding_config_sha256,
            "vector_backend": vector_backend,
            "vector_config_sha256": vector_config_sha256,
            "python": sys.version.split()[0],
        },
        "metrics": metrics,
        "metrics_by_arm_and_noise": arm_slices,
        "metrics_by_task_kind": kind_slices,
        "noise_degradation_low_to_high": degradation,
        "noise_levels": level_results,
    }
