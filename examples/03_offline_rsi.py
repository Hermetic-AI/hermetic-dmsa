"""无网络、开发集专用的离线 RSI 配对回放示例。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dmsa.gradient import PolicyRegistry, ReplayCase, RetrievalPolicy, run_replay
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.observability.logging import get_logger


def main() -> None:
    """构造确定性语料; 回放候选并单独发布到本地注册表。"""
    logger = get_logger("dmsa.offline_rsi.example")
    with tempfile.TemporaryDirectory(prefix="dmsa-rsi-") as directory:
        memory = CodingMemory(Path(directory) / "memory.sqlite")
        try:
            memory.add(
                "request-1",
                "demo-user",
                "session-1",
                [SourceMessage("assistant", "alpha anchor")],
            )
            memory.add(
                "request-2",
                "demo-user",
                "session-2",
                [SourceMessage("assistant", "independent target evidence")],
            )
            baseline = RetrievalPolicy(SearchPolicy(candidate_limit=1))
            candidate = RetrievalPolicy(SearchPolicy(candidate_limit=10))
            relevant = memory.search("alpha target", "demo-user", 2, candidate.search)[1].id
            report = run_replay(
                memory,
                baseline,
                candidate,
                (ReplayCase("case-1", "demo-user", "alpha target", (relevant,), "demo"),),
                top_k=2,
                max_chars=12000,
                max_tokens=12000,
                development_only=True,
            )
            registry = PolicyRegistry(Path(directory) / "registry.sqlite")
            try:
                version = registry.register(candidate, report, allow_bootstrap=True)
                logger.info("offline RSI candidate published", version=version)
            finally:
                registry.close()
        finally:
            memory.close()


if __name__ == "__main__":
    main()
