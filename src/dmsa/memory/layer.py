"""MemoryLayer 公开入口与依赖接线."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from pmd import (
    ProvenanceDAG,
)

from dmsa.errors import (
    MemoryLayerError,
)
from dmsa.memory._search import MemorySearch
from dmsa.memory._state import MemorySerialization
from dmsa.memory._write import MemoryWrite
from dmsa.observability import get_logger
from dmsa.types import MemoryLayerConfig

_logger = get_logger("dmsa.memory.layer")


class MemoryLayer(MemoryWrite, MemorySearch, MemorySerialization):
    """通过稳定公共 API 组合记忆存储, 检索和序列化能力."""

    def __init__(
        self,
        *,
        config: MemoryLayerConfig | None = None,
        embedder: Any | None = None,
        store: Any | None = None,
    ) -> None:
        """初始化存储和嵌入器依赖."""
        self._config = config or MemoryLayerConfig()
        self._embedder = embedder
        self._lock = threading.RLock()

        try:
            if store is None:
                if self._config.storage_path == ":memory:":
                    from pmd.store.memory_store import InMemoryStore

                    store = InMemoryStore()
                else:
                    from pmd.store.sqlite_store import SQLiteStore

                    path = Path(self._config.storage_path).expanduser()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    store = SQLiteStore(path=str(path))
            self._store = store
            self._dag = ProvenanceDAG(store=store, embedder=embedder)
        except Exception as exc:
            raise MemoryLayerError(
                f"failed to initialize memory storage: {exc}",
                details={"storage_path": self._config.storage_path},
            ) from exc

    @property
    def dag(self) -> ProvenanceDAG:
        """暴露给 L2/L3 模块调用(不应被最终用户直接使用)."""
        return self._dag

    @property
    def store(self) -> Any:
        """存储后端."""
        return self._store

    @property
    def embedder(self) -> Any:
        """嵌入器."""
        return self._embedder

    def attach_embedder(self, embedder: Any) -> None:
        """构造后注入嵌入器(供 L4 server 做默认接线).

        Args:
            embedder: 任何实现 ``embed(str) -> list[float]`` 且带 ``name``
                属性的对象(如 :class:`dmsa.gradient.HashEmbedder`).

        Raises:
            MemoryLayerError: embedder 为 None 或缺少 ``embed`` 方法.
        """
        if embedder is None or not callable(getattr(embedder, "embed", None)):
            raise MemoryLayerError(
                "attach_embedder requires an object with an embed() method",
                details={"embedder": type(embedder).__name__},
            )
        with self._lock:
            self._embedder = embedder
            # PMD 的 DAG 在构造时缓存了 embedder,需同步替换
            self._dag._embedder = embedder
        _logger.info("embedder attached", embedder=getattr(embedder, "name", "?"))

    def __len__(self) -> int:
        """返回节点数量."""
        with self._lock:
            return len(self._dag)

    def __contains__(self, node_id: object) -> bool:
        """检查节点是否存在."""
        with self._lock:
            return node_id in self._dag
