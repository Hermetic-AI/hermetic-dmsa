"""写入路径: 幂等预检、门控判定与事务发布.

写入逻辑从 CodingMemory 门面拆出, 门面只保留公开 API 与委托, 使单个文件
长度保持在项目规范内. 幂等预检、门控判定、向量化与发布的先后顺序不变;
稠密通道故障时降级为无向量写入, 由 reindex 补算, 不再让整条写入丢失.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager

from dmsa.memory.coding.admission import (
    DEGRADED_EMBEDDING,
    DEGRADED_NONE,
    DEGRADED_VECTOR_INDEX,
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
from dmsa.observability import observe_count


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

        稠密通道 (嵌入服务、向量索引) 故障时降级为无向量写入: 原文、切片、
        倒排词项与 FTS 照常落库, 稀疏通道立即可用, 向量由 reindex_vectors
        事后补算. 降级原因写在返回值的 degraded 字段里.
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
        # 分块数量决定 embedding 批量大小，是写入侧唯一需要看见的规模量。
        observe_count("dmsa.write.chunks", len(chunks))
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
        vectors, degraded = self._embed_vectors(chunks)
        if vectors and not self._publish_vectors(scope, request_id, chunks, vectors):
            degraded = DEGRADED_VECTOR_INDEX
        with self._operation():
            revision = self.store.add(
                request_id,
                scope,
                session_id,
                parsed,
                chunks,
                vectors,
                self.retriever.fingerprint if vectors else None,
                author=author,
                rival=rival,
            )
        return AdmissionResult(revision, verdict, degraded)

    def _embed_vectors(self, chunks: Sequence[Chunk]) -> tuple[list[tuple[float, ...]], str]:
        """计算切片向量; 嵌入服务故障时降级为空向量而不是丢弃整条写入."""
        if self.retriever.embedder is None or not chunks:
            return [], DEGRADED_NONE
        try:
            return self.retriever.embed_new([chunk.text for chunk in chunks]), DEGRADED_NONE
        except CodingMemoryError:
            observe_count("dmsa.write.embedding_degraded", len(chunks))
            return [], DEGRADED_EMBEDDING

    def _publish_vectors(
        self,
        scope: str,
        request_id: str,
        chunks: Sequence[Chunk],
        vectors: list[tuple[float, ...]],
    ) -> bool:
        """在提交前发布向量, 避免远端写阻塞数据库事务.

        返回是否发布成功. 远端索引故障时只降级不抛错: 向量仍随本次事务写进
        SQLite, 事后可由 reindex_vectors 原样重发布, 不需要重新嵌入.
        """
        index = self.retriever.vector_index
        fingerprint = self.retriever.fingerprint
        if index is None or fingerprint is None:
            return True
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
        except Exception:
            observe_count("dmsa.write.vector_index_degraded", len(ids))
            return False
        return True
