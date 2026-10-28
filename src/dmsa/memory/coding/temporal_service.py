"""时序 as-of 检索服务: 按写入序号或写入时刻回看历史证据."""

from __future__ import annotations

from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryValidationError,
    SearchPolicy,
    require_identifier,
    require_optional_identifier,
    require_text,
)
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.search_flow import operation, run_search
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.temporal import AsOf, TemporalResult

# 单次时序检索允许的最大返回条数.
MAX_TOP_K = 50


class TemporalService:
    """在同一作用域上执行时序 as-of 检索."""

    def __init__(
        self, store: CodingStore, retriever: CodingRetriever, policy: SearchPolicy
    ) -> None:
        """绑定存储、检索器与默认检索策略."""
        self.store = store
        self.retriever = retriever
        self.policy = policy

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy | None = None,
        repo: str = "",
        as_of: AsOf | None = None,
    ) -> TemporalResult:
        """按时序坐标检索历史证据, 并回报作用域当前的写入序号."""
        require_text(query, "query")
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise CodingMemoryValidationError(f"top_k must be an integer between 1 and {MAX_TOP_K}")
        selected = policy or self.policy
        if not isinstance(selected, SearchPolicy):
            raise CodingMemoryValidationError("policy must be SearchPolicy")
        if not isinstance(as_of, AsOf):
            raise CodingMemoryValidationError("as_of must be AsOf")
        scope = scope_key(user_id, repo)
        hits = run_search(self.store, self.retriever, selected, query, scope, top_k, as_of)
        with operation(self.store):
            revision = self.store.revision(scope)
        return TemporalResult(query=query, hits=tuple(hits), as_of=as_of, scope_revision=revision)

    def scope_revision(self, user_id: str, repo: str = "") -> int:
        """返回作用域当前的写入序号, 作为 as-of 坐标的取值上界."""
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        scope = scope_key(user_id, repo)
        with operation(self.store):
            return self.store.revision(scope)
