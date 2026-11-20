"""Lineage 通道的确定性消融: 同一语料比较默认策略与 lineage 策略.

这不是官方评测, 也不测编码成功率; 它只回答一个可复现的问题:
同会话经验更新后, 默认检索是否隐藏"已被取代"的信息, 打开 lineage 通道后
该信息是否能随新卡送达 Agent。无 LLM、无网络调用。

用法::

    python -m benchmarks.coding.lineage_ablation [--out results/lineage.json]
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME

QUERY = "失败尝试 修复文件 test_cache.py"
TURN = (
    SourceMessage("user", "tests/test_cache.py 的 test_load 失败，需要修复缓存。"),
    SourceMessage("assistant", "根因：读取时没有比较版本号。"),
    SourceMessage(
        "assistant",
        "修复：\n```python path=tests/test_cache.py\ndef test_load():\n"
        "    assert load_cache()\n```\n验证：pytest tests/test_cache.py::test_load -q: 1 passed",
    ),
)


def run(root: Path) -> dict[str, Any]:
    """在两个臂上使用同一份写入语料, 返回可比较的观测量."""
    memory = CodingMemory(root / "lineage.db")
    try:
        memory.add("r1", "ablation", "s", list(TURN))
        memory.add(
            "r2",
            "ablation",
            "s",
            [SourceMessage("assistant", "补充：再覆盖一次边界用例。")],
        )
        rows = memory.store.rows("ablation")
        cards = [row for row in rows if row["role"] == "episode"]
        result: dict[str, Any] = {
            "cards": len(cards),
            "superseded_cards": sum(1 for row in cards if row["outcome"] == SUPERSEDED_OUTCOME),
            "arms": {},
        }
        for name, policy in (
            ("default", SearchPolicy()),
            ("lineage", SearchPolicy(lineage_window=1)),
        ):
            hits = memory.search(QUERY, "ablation", 10, policy)
            result["arms"][name] = {
                "hits": len(hits),
                "supersedes_marker": sum("[supersedes " in hit.content for hit in hits),
                "superseded_marker": sum("[superseded by" in hit.content for hit in hits),
                "superseded_outcome": sum("outcome=superseded" in hit.content for hit in hits),
            }
        return result
    finally:
        memory.close()


def main() -> None:
    """打印两臂结果, 可选写入 JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        result = run(Path(directory))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
