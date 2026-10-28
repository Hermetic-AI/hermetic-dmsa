"""程序性记忆服务: 沉淀可复用流程并按触发语境匹配.

流程与事实记忆分开存放: 事实回答「发生过什么」, 流程回答「下次按什么顺序做」.
只认显式的步骤、前置条件与验证说明, 不推断未写出的步骤.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    SourceMessage,
    normalize_messages,
    require_identifier,
    require_optional_identifier,
    require_text,
)
from dmsa.memory.coding.procedure_store import (
    ProcedureMatch,
    match_procedures,
    save_procedure,
)
from dmsa.memory.coding.procedures import Procedure, build_procedure, match_terms
from dmsa.memory.coding.storage import CodingStore


class ProcedureService:
    """在单个作用域上沉淀与匹配流程记忆."""

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

    def add(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        messages: Iterable[SourceMessage],
        repo: str = "",
    ) -> Procedure | None:
        """从会话中沉淀一条可复用流程; 没有显式有序步骤时返回 None."""
        require_identifier(request_id, "request_id")
        require_identifier(user_id, "user_id")
        require_identifier(session_id, "session_id")
        require_optional_identifier(repo, "repo")
        parsed = normalize_messages(messages)
        procedure = build_procedure(parsed)
        if procedure is None:
            return None
        scope = scope_key(user_id, repo)
        with self._operation():
            save_procedure(self.store.db, scope, request_id, procedure)
        return procedure

    def search(
        self, query: str, user_id: str, top_k: int = 3, repo: str = ""
    ) -> list[ProcedureMatch]:
        """按触发语境匹配 scope 内的流程, 返回命中词项最多的若干条."""
        require_text(query, "query")
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(top_k) is not int or top_k < 1:
            raise CodingMemoryValidationError("top_k must be a positive integer")
        scope = scope_key(user_id, repo)
        terms = match_terms(query)
        with self._operation():
            return match_procedures(self.store.db, scope, terms, limit=top_k)
