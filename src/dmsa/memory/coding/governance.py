"""记忆治理: 曝光记账、信任报告、反馈记账与遗忘淘汰.

治理动作全部通过同一个 CodingStore 连接执行, 与写入路径共享锁, 保证
观测计数不会出现半更新状态. 淘汰只打标记不删原文, 原文仍可按 ID 追溯.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.trust_store import (
    apply_verdict,
    evict_below,
    now_seconds,
    record_exposure,
    scope_stats,
    unevict,
)

SECONDS_PER_DAY = 86400


class MemoryGovernance:
    """对单个 scope 的记忆条目做观测记账与淘汰."""

    def __init__(self, store: CodingStore) -> None:
        """绑定存储连接."""
        self.store = store

    def record_exposure(self, user_id: str, chunk_ids: Iterable[str], repo: str = "") -> int:
        """记录条目被检索返回的曝光次数并刷新最后使用时间."""
        scope = scope_key(user_id, repo)
        with self.store.lock, self.store.db:
            return record_exposure(self.store.db, scope, chunk_ids)

    def trust_report(self, user_id: str, repo: str = "") -> list[dict[str, Any]]:
        """返回 scope 内全部条目的信任度与观测计数."""
        scope = scope_key(user_id, repo)
        with self.store.lock:
            now = now_seconds()
            return [stat.to_dict(now=now) for stat in scope_stats(self.store.db, scope)]

    def record_verdict(
        self,
        user_id: str,
        chunk_ids: Iterable[str],
        repo: str = "",
        *,
        confirmed: bool = False,
        contradicted: bool = False,
        verified: bool = False,
    ) -> int:
        """按反馈或任务结果更新条目的确认、反驳与验证计数."""
        scope = scope_key(user_id, repo)
        with self.store.lock, self.store.db:
            return apply_verdict(
                self.store.db,
                scope,
                chunk_ids,
                confirmed=confirmed,
                contradicted=contradicted,
                verified=verified,
            )

    def forget(
        self, user_id: str, repo: str = "", *, floor: float = 0.1, min_age_days: int = 30
    ) -> tuple[str, ...]:
        """淘汰长期未被使用且信任度低于阈值的条目, 返回被淘汰的 ID."""
        scope = scope_key(user_id, repo)
        with self.store.lock, self.store.db:
            return evict_below(
                self.store.db,
                scope,
                floor=floor,
                min_age_seconds=min_age_days * SECONDS_PER_DAY,
            )

    def restore(self, user_id: str, chunk_ids: Iterable[str], repo: str = "") -> int:
        """取消淘汰标记, 让条目重新参与检索."""
        scope = scope_key(user_id, repo)
        with self.store.lock, self.store.db:
            return unevict(self.store.db, scope, chunk_ids)
