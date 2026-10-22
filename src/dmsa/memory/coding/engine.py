"""CodingMemory 稳定公开门面。."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from dmsa.memory.coding.extraction import extract
from dmsa.memory.coding.models import (
    CodingMemoryValidationError,
    CorpusState,
    Embedder,
    SearchHit,
    SearchPolicy,
    SourceMessage,
    require_text,
)
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore


class CodingMemory:
    """提供 scope 隔离、耐久写入与来源可追溯检索。."""

    def __init__(
        self, path: str | Path, embedder: Embedder | None = None, policy: SearchPolicy | None = None
    ) -> None:
        """打开数据库并绑定可选检索策略和嵌入器。."""
        self.store = CodingStore(path)
        self.retriever = CodingRetriever(self.store, embedder)
        self.policy = policy or SearchPolicy()

    @staticmethod
    def _messages(messages: Iterable[SourceMessage]) -> tuple[SourceMessage, ...]:
        """验证只含标准化文本的有序来源消息。."""
        try:
            result = tuple(messages)
        except TypeError as exc:
            raise CodingMemoryValidationError("messages must be iterable") from exc
        if not result:
            raise CodingMemoryValidationError("messages must not be empty")
        for message in result:
            if not isinstance(message, SourceMessage):
                raise CodingMemoryValidationError("messages must contain SourceMessage objects")
            require_text(message.role, "role")
            require_text(message.content, "content")
            if message.timestamp is not None and (
                type(message.timestamp) is not int or message.timestamp < 0
            ):
                raise CodingMemoryValidationError("timestamp must be a nonnegative integer")
        return result

    def add(
        self, request_id: str, user_id: str, session_id: str, messages: Iterable[SourceMessage]
    ) -> int:
        """幂等写入完整请求并返回 scope revision。."""
        require_text(request_id, "request_id")
        require_text(user_id, "user_id")
        require_text(session_id, "session_id")
        parsed = self._messages(messages)
        payload_hash = self.store.payload_hash(session_id, parsed)
        previous = self.store.existing_request(request_id, user_id, payload_hash)
        if previous is not None:
            return previous
        chunks = extract(parsed)
        vectors = self.retriever.embed_new([chunk.text for chunk in chunks])
        return self.store.add(
            request_id, user_id, session_id, parsed, chunks, vectors, self.retriever.fingerprint
        )

    def search(
        self, query: str, user_id: str, top_k: int, policy: SearchPolicy | None = None
    ) -> list[SearchHit]:
        """检索 scope 内证据; policy 可用于无副作用配对回放。."""
        require_text(query, "query")
        require_text(user_id, "user_id")
        return self.retriever.search(query, user_id, top_k, policy or self.policy)

    def delete_scope(self, user_id: str) -> None:
        """删除该 scope 的所有来源和索引。."""
        self.store.delete_scope(user_id)

    def corpus_fingerprint(self, user_ids: Iterable[str]) -> str:
        """返回有序 scope revision 快照的确定性指纹。."""
        return self.store.corpus_state(user_ids).fingerprint

    def corpus_state(self, user_ids: Iterable[str]) -> CorpusState:
        """返回同一只读事务取得的多 scope revision 与 fingerprint."""
        return self.store.corpus_state(user_ids)

    def close(self) -> None:
        """关闭 SQLite 连接。."""
        self.store.close()
