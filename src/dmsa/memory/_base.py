"""记忆层内部共享状态声明."""

from __future__ import annotations

from typing import Any

from pmd import ProvenanceDAG

from dmsa.types import MemoryLayerConfig


class MemoryState:
    """声明各职责模块共同使用的状态."""

    _config: MemoryLayerConfig
    _embedder: Any
    _store: Any
    _lock: Any
    _dag: ProvenanceDAG
