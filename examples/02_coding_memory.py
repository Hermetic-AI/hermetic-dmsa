# ruff: noqa: RUF002
"""离线 Coding Memory 最小示例，无 API key。"""
from __future__ import annotations

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.observability import get_logger


def main() -> None:
    """写入并检索一条本地证据。"""
    memory = CodingMemory(":memory:", policy=SearchPolicy(dense_weight=0.0))
    try:
        memory.add("example-1", "developer", "demo", [SourceMessage("user", "SQLite transaction rollback")])
        for hit in memory.search("transaction rollback", "developer", 3):
            get_logger(__name__).info("coding memory result", record_id=hit.id, content=hit.content, score=hit.score)
    finally:
        memory.close()


if __name__ == "__main__":
    main()

