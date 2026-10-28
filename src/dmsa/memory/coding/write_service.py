"""写入路径: 幂等预检、门控判定与事务发布.

写入逻辑从 CodingMemory 门面拆出, 门面只保留公开 API 与委托, 使单个文件
长度保持在项目规范内. 拆分不改变语义: 幂等预检、门控判定、向量化与发布的
先后顺序完全不变.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager

from dmsa.memory.coding.admission import (
    REASON_CONFLICT,
    REASON_IDEMPOTENT,
    AdmissionDecision,
    AdmissionResult,
    evaluate,
)
from dmsa.memory.coding.extraction import Chunk, extract
from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    SearchPolicy,
    SourceMessage,
    normalize_messages,
    require_identifier,
    require_optional_identifier,
)
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore


class WriteService:
    """在单个作用域上执行一次受门控保护的写入."""

    def __init__(
        self, store: CodingStore, retriever: CodingRetriever, policy: SearchPolicy
    ) -> None:
        """绑定存储、检索器与默认策略."""
        self.store = store
        self.retriever = retriever
        self.policy = policy

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
        policy: SearchPolicy | None = None,
        repo: str = "",
        author: str = "",
    ) -> AdmissionResult:
        """写入请求并返回准入判定.

        门控判定为重复或空内容时不落盘, scope revision 保持不变, 调用方
        可从返回的 decision 里读到原因. 跨作者的近似重复不判为重复, 而是
        判为冲突候选: 写入保留, 同时登记一条待仲裁的写冲突.
        """
        require_identifier(request_id, "request_id")
        require_identifier(user_id, "user_id")
        require_identifier(session_id, "session_id")
        require_optional_identifier(repo, "repo")
        require_optional_identifier(author, "author")
        parsed = normalize_messages(messages)
        selected = policy or self.policy
        if not isinstance(selected, SearchPolicy):
            raise CodingMemoryValidationError("policy must be SearchPolicy")
        scope = scope_key(user_id, repo)
        payload_hash = self.store.payload_hash(session_id, parsed)
        with self._operation():
            previous = self.store.existing_request(request_id, scope, payload_hash)
        if previous is not None:
            decision = AdmissionDecision(True, REASON_IDEMPOTENT, 1.0)
            return AdmissionResult(previous, decision)
        chunks = extract(parsed, selected.chunk_size, selected.chunk_overlap)
        with self._operation():
            verdict = evaluate(
                self.store.db,
                scope,
                tuple(chunk.text for chunk in chunks),
                threshold=selected.admission_similarity,
                author=author,
            )
        if not verdict.admitted:
            return AdmissionResult(self.store.revision(scope), verdict)
        rival: tuple[str, float] | None = None
        if verdict.reason == REASON_CONFLICT and verdict.duplicate_of:
            rival = (verdict.duplicate_of, 1.0 - verdict.novelty)
        vectors = self.retriever.embed_new([chunk.text for chunk in chunks])
        self._publish_vectors(scope, request_id, chunks, vectors)
        with self._operation():
            revision = self.store.add(
                request_id,
                scope,
                session_id,
                parsed,
                chunks,
                vectors,
                self.retriever.fingerprint,
                author=author,
                rival=rival,
            )
        return AdmissionResult(revision, verdict)

    def _publish_vectors(
        self,
        scope: str,
        request_id: str,
        chunks: Sequence[Chunk],
        vectors: list[tuple[float, ...]],
    ) -> None:
        """在提交前发布向量, 避免远端写阻塞数据库事务."""
        index = self.retriever.vector_index
        fingerprint = self.retriever.fingerprint
        if index is None or fingerprint is None:
            return
        ids = [
            self.store.chunk_id(
                self.store.message_id(scope, request_id, chunk.message_index),
                chunk.start,
                chunk.end,
            )
            for chunk in chunks
        ]
        try:
            index.upsert(scope, fingerprint, ids, vectors)
        except Exception as exc:
            raise CodingMemoryError("vector index write failed; memory was not committed") from exc
