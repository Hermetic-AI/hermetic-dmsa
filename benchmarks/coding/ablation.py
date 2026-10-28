"""代码锚点通道的开消融评测: 在同一预算下比较是否启用锚点召回.

评测语料是自建的结构化对照集: gold 证据是补丁/代码块形式的结构化历史,
干扰项是词面高度相似的散文描述. 目标是验证结构化锚点能否把"可定位的证据"
排在"看起来相似但无法定位的散文"之前. 这是开发用难例, 不冒充官方评测数据.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.coding.dataset import ndcg
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage

# 消融组: 只改变锚点通道权重, 其余通道、预算与 top_k 完全一致.
ARMS = {"no_anchor": 0.0, "with_anchor": 2.0}
TOP_K = 5


@dataclass(frozen=True)
class AblationTask:
    """一条消融任务及其 gold 与干扰证据."""

    task_id: str
    query: str
    gold: str
    distractors: tuple[str, ...]


TASKS = (
    AblationTask(
        task_id="anchor-cache-race",
        query="load_cache 在版本检查后替换缓存导致读到旧值",
        gold=(
            "diff --git a/src/cache.py b/src/cache.py\n"
            "--- a/src/cache.py\n"
            "+++ b/src/cache.py\n"
            "@@ -20,6 +20,9 @@ class CacheStore:\n"
            "+    def load_cache(self, key):\n"
            "+        with self._lock:\n"
            "+            return self._entries.get(key)\n"
        ),
        distractors=(
            "load_cache 的版本检查放在锁外, 并发写入时旧值会覆盖新值, 建议把检查移到锁内。",
            "缓存条目在使用前要校验版本, 否则会读到过期的数据, 这一点在文档里也提到过。",
            "The cache layer keeps a version field so stale entries can be detected later.",
            "如果缓存没有命中, 应该回源到数据库, 再把结果写回缓存以提升命中率。",
        ),
    ),
    AblationTask(
        task_id="anchor-parser-timeout",
        query="client 请求超时需要在 transport 之前校验非零",
        gold=(
            "diff --git a/src/client.py b/src/client.py\n"
            "--- a/src/client.py\n"
            "+++ b/src/client.py\n"
            "@@ -8,6 +8,10 @@ class Client:\n"
            "+    def request(self, path, timeout=None):\n"
            "+        if timeout is not None and timeout <= 0:\n"
            "+            raise ValueError('timeout must be positive')\n"
        ),
        distractors=(
            "客户端超时参数应该允许零值, 表示不设置超时, 由 transport 层自行决定。",
            "timeout 配置在全局常量里, 单次请求无法覆盖, 只能整体修改默认行为。",
            "Requests may carry a timeout field, but the transport layer owns the deadline.",
            "把超时写死成三十秒可以避免请求悬挂, 但无法满足长连接场景。",
        ),
    ),
)


def _build_memory(path: Path) -> tuple[CodingMemory, dict[str, str]]:
    """写入消融语料, 返回记忆实例与 gold 切片 ID 映射."""
    memory = CodingMemory(path)
    gold_ids: dict[str, str] = {}
    for task in TASKS:
        memory.add(
            f"gold-{task.task_id}",
            "ablation",
            task.task_id,
            [SourceMessage("assistant", task.gold)],
        )
        for index, distractor in enumerate(task.distractors):
            memory.add(
                f"noise-{task.task_id}-{index}",
                "ablation",
                task.task_id,
                [SourceMessage("assistant", distractor)],
            )
    for task in TASKS:
        hits = memory.search(task.query, "ablation", TOP_K, SearchPolicy(anchor_weight=0.0))
        for hit in hits:
            if task.gold[:40] in hit.content:
                gold_ids[task.task_id] = hit.id
                break
        else:
            gold_ids[task.task_id] = ""
    return memory, gold_ids


def _run_arm(
    memory: CodingMemory, gold_ids: dict[str, str], anchor_weight: float
) -> dict[str, Any]:
    """在固定预算下运行一个消融组并统计检索指标."""
    policy = SearchPolicy(anchor_weight=anchor_weight, dense_weight=0.0)
    recalls: list[float] = []
    ndcgs: list[float] = []
    misleading = 0
    tokens: list[int] = []
    for task in TASKS:
        hits = memory.search(task.query, "ablation", TOP_K, policy)
        ranked = [hit.id for hit in hits]
        relevant = {gold_ids[task.task_id]} if gold_ids[task.task_id] else set()
        recalls.append(1.0 if relevant and relevant & set(ranked) else 0.0)
        ndcgs.append(ndcg(ranked, relevant))
        if relevant and not relevant & set(ranked):
            misleading += 1
        tokens.append(sum(len(hit.content) for hit in hits))
    return {
        "anchor_weight": anchor_weight,
        "evidence_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        "ndcg": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
        "misleading_memory_rate": misleading / len(TASKS) if TASKS else 0.0,
        "evidence_chars": sum(tokens) / len(tokens) if tokens else 0.0,
    }


def run_ablation() -> dict[str, Any]:
    """执行锚点通道开关消融, 返回各组真实指标."""
    with tempfile.TemporaryDirectory() as raw:
        memory, gold_ids = _build_memory(Path(raw) / "ablation.sqlite")
        try:
            arms = {name: _run_arm(memory, gold_ids, weight) for name, weight in ARMS.items()}
        finally:
            memory.close()
    return {
        "tasks": len(TASKS),
        "top_k": TOP_K,
        "arms": arms,
        "note": "自建结构化对照集, 仅用于开发期消融, 不代表官方成绩",
    }


def main() -> int:
    """打印消融结果 JSON."""
    # Windows 控制台默认 cp936, 不重配会把中文说明打成乱码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run_ablation(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
