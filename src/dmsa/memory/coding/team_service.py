"""团队作用域服务: 共享作用域键与跨作者写冲突的仲裁.

团队成员共享同一个作用域, 因此同一问题可能被不同成员各写一遍. 与个人作用域
不同, 跨作者的近似重复不是噪音: 双方可能给出相反结论, 所以这类写入被保留
下来并登记为冲突候选, 交由仲裁决定哪一方是权威版本.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    require_identifier,
    require_optional_identifier,
)
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.teams import (
    ConflictResolution,
    WriteConflict,
    find_conflict,
    list_conflicts,
    resolve_conflict,
    team_scope,
)


class TeamMemoryService:
    """在单个作用域上列出并仲裁跨作者写冲突."""

    def __init__(self, store: CodingStore) -> None:
        """绑定存储连接."""
        self.store = store

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """同一连接的读取与事务互斥, 防止观察到未提交的部分请求."""
        with self.store.lock:
            try:
                yield
            except (sqlite3.Error, OverflowError) as exc:
                raise CodingMemoryError("coding memory database operation failed") from exc

    @staticmethod
    def team_scope(team_id: str, repo: str = "") -> str:
        """返回团队共享作用域键: 成员共享同一证据空间, 仓库仍互相隔离."""
        return team_scope(team_id, repo)

    def list_conflicts(
        self, user_id: str, repo: str = "", *, status: str = "", limit: int = 20
    ) -> list[WriteConflict]:
        """列出作用域内的跨作者写冲突; status 为空时返回全部状态."""
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise CodingMemoryValidationError("limit must be an integer between 1 and 200")
        scope = scope_key(user_id, repo)
        with self._operation():
            return list_conflicts(self.store.db, scope, status=status, limit=limit)

    def resolve_conflict(
        self, user_id: str, conflict_id: str, resolution: str, repo: str = ""
    ) -> ConflictResolution:
        """按仲裁结论关闭一条写冲突, 并把结论写回双方的信任计数.

        不带仓库时会在该用户的作用域及其仓库子域里查找这条冲突.
        重复仲裁同一条冲突不会重复记账.
        """
        require_identifier(user_id, "user_id")
        require_identifier(conflict_id, "conflict_id")
        require_optional_identifier(repo, "repo")
        with self._operation():
            scopes = (
                (scope_key(user_id, repo),)
                if repo
                else tuple(dict.fromkeys((user_id, *self.store.sub_scopes(user_id))))
            )
            for scope in scopes:
                if find_conflict(self.store.db, scope, conflict_id) is None:
                    continue
                with self.store.db:
                    return resolve_conflict(self.store.db, scope, conflict_id, resolution)
            raise CodingMemoryValidationError("unknown conflict id")
