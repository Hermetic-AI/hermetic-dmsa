"""CodingMemory 稳定公开门面。.

门面只保留公开 API 与参数转交, 具体实现放在同层的服务模块里, 这样每个
文件都留在项目规范的长度内. 各服务的语义与拆分前一致.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from dmsa.memory.coding.admission import AdmissionResult
from dmsa.memory.coding.consolidation_api import ConsolidationApi
from dmsa.memory.coding.consolidation_service import ConsolidationService
from dmsa.memory.coding.explanation import RecallExplanation
from dmsa.memory.coding.governance import MemoryGovernance
from dmsa.memory.coding.hybrid_api import HybridApi
from dmsa.memory.coding.hybrid_service import HybridService
from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    CorpusState,
    Embedder,
    SearchHit,
    SearchPolicy,
    SourceMessage,
    require_identifier,
    require_optional_identifier,
    require_text,
)
from dmsa.memory.coding.procedure_service import ProcedureService
from dmsa.memory.coding.procedure_store import ProcedureMatch
from dmsa.memory.coding.procedures import Procedure
from dmsa.memory.coding.recall_service import RecallService
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.search_flow import operation, run_search
from dmsa.memory.coding.snapshot import RepoSnapshot, build_snapshot
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.team_service import TeamMemoryService
from dmsa.memory.coding.teams import ConflictResolution, WriteConflict
from dmsa.memory.coding.temporal_api import TemporalApi
from dmsa.memory.coding.temporal_service import TemporalService
from dmsa.memory.coding.vector_index import VectorIndex
from dmsa.memory.coding.write_service import WriteService


class CodingMemory(ConsolidationApi, HybridApi, TemporalApi):
    """提供 scope 隔离、耐久写入与来源可追溯检索。."""

    def __init__(
        self,
        path: str | Path,
        embedder: Embedder | None = None,
        policy: SearchPolicy | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        """打开数据库并绑定可选检索策略和嵌入器。."""
        try:
            self.store = CodingStore(path)
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise CodingMemoryError("cannot initialize coding memory storage") from exc
        try:
            if vector_index is not None and embedder is None:
                raise CodingMemoryValidationError("vector_index requires an embedder")
            self.vector_index = vector_index
            self.retriever = CodingRetriever(self.store, embedder, vector_index)
            self.governance = MemoryGovernance(self.store)
            self.policy = policy or SearchPolicy()
            if not isinstance(self.policy, SearchPolicy):
                raise CodingMemoryValidationError("policy must be SearchPolicy")
            self.writes = WriteService(self.store, self.retriever, self.policy)
            self.procedures = ProcedureService(self.store)
            self.teams = TeamMemoryService(self.store)
            self.recall = RecallService(self.store, self.retriever, self.policy)
            self.consolidations = ConsolidationService(self.store, self.retriever, self.policy)
            self.hybrid = HybridService(self.store, self.retriever, self.policy)
            self.temporal = TemporalService(self.store, self.retriever, self.policy)
        except Exception:
            self.store.close()
            raise

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """同一连接的读取与事务互斥, 防止观察到未提交的部分请求."""
        with operation(self.store):
            yield

    def add(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        messages: Iterable[SourceMessage],
        policy: SearchPolicy | None = None,
        repo: str = "",
        author: str = "",
    ) -> int:
        """幂等写入完整请求并返回 scope revision.

        `repo` 是 scope 的子维度: 传入后写入与检索都按仓库隔离, 便于同一用户
        在多个仓库之间切换时互不串证据. `author` 标记本次写入的作者, 团队
        作用域下用于识别跨作者的写冲突.
        """
        return self.add_guarded(
            request_id, user_id, session_id, messages, policy=policy, repo=repo, author=author
        ).revision

    def add_guarded(
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
        return self.writes.add(
            request_id,
            user_id,
            session_id,
            messages,
            policy=policy,
            repo=repo,
            author=author,
        )

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy | None = None,
        repo: str = "",
    ) -> list[SearchHit]:
        """检索 scope 内证据; policy 可用于无副作用配对回放。

        外部调用 (查询向量化、远端向量检索) 在数据库锁之外执行, 避免慢上游
        阻塞同进程的其他 Add/Search。
        """
        require_text(query, "query")
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        selected = policy or self.policy
        if not isinstance(selected, SearchPolicy):
            raise CodingMemoryValidationError("policy must be SearchPolicy")
        scope = scope_key(user_id, repo)
        return run_search(self.store, self.retriever, selected, query, scope, top_k)

    def search_explained(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy | None = None,
        repo: str = "",
        hops: int = 1,
    ) -> RecallExplanation:
        """检索并返回召回解释: 每条证据的命中通道、命中词项与迭代轮次.

        `hops` 大于 1 时会做多跳迭代: 用首轮命中的路径与符号扩展查询再召回
        一轮, 扩展只影响候选范围, 最终排序口径与单跳一致.
        """
        return self.recall.search(query, user_id, top_k, policy=policy, repo=repo, hops=hops)

    def add_procedure(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        messages: Iterable[SourceMessage],
        repo: str = "",
    ) -> Procedure | None:
        """从会话中沉淀一条可复用流程; 没有显式有序步骤时返回 None."""
        return self.procedures.add(request_id, user_id, session_id, messages, repo)

    def search_procedures(
        self, query: str, user_id: str, top_k: int = 3, repo: str = ""
    ) -> list[ProcedureMatch]:
        """按触发语境匹配 scope 内的流程, 返回命中词项最多的若干条."""
        return self.procedures.search(query, user_id, top_k, repo)

    @staticmethod
    def team_scope(team_id: str, repo: str = "") -> str:
        """返回团队共享作用域键: 团队成员共享同一证据空间, 仓库仍互相隔离."""
        return TeamMemoryService.team_scope(team_id, repo)

    def conflicts(
        self, user_id: str, repo: str = "", *, status: str = "", limit: int = 20
    ) -> list[WriteConflict]:
        """列出作用域内的跨作者写冲突; status 为空时返回全部状态."""
        return self.teams.list_conflicts(user_id, repo, status=status, limit=limit)

    def resolve_conflict(
        self, user_id: str, conflict_id: str, resolution: str, repo: str = ""
    ) -> ConflictResolution:
        """按仲裁结论关闭一条写冲突, 并把结论写回双方的信任计数."""
        return self.teams.resolve_conflict(user_id, conflict_id, resolution, repo)

    def save_repo_snapshot(self, user_id: str, root: str | Path, repo: str = "") -> RepoSnapshot:
        """生成并保存 scope 的仓库符号快照, 供检索标记失效证据."""
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        scope = scope_key(user_id, repo)
        snapshot = build_snapshot(root)
        with self._operation():
            self.store.save_snapshot(scope, snapshot)
        return snapshot

    def delete_scope(self, user_id: str, repo: str = "") -> None:
        """删除该 scope 的所有来源和索引; 不带 repo 时连同仓库子域一起清理."""
        require_text(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        with self._operation():
            scopes = (
                (scope_key(user_id, repo),)
                if repo
                else tuple(dict.fromkeys((user_id, *self.store.sub_scopes(user_id))))
            )
            for scope in scopes:
                self.store.delete_scope(scope)
        if self.vector_index is None:
            return
        for scope in scopes:
            try:
                self.vector_index.delete_scope(scope)
            except Exception as exc:
                raise CodingMemoryError("vector index scope deletion failed") from exc

    def corpus_fingerprint(self, user_ids: Iterable[str]) -> str:
        """返回有序 scope revision 快照的确定性指纹。."""
        return self.corpus_state(user_ids).fingerprint

    def corpus_state(self, user_ids: Iterable[str]) -> CorpusState:
        """返回同一只读事务取得的多 scope revision 与 fingerprint."""
        try:
            users = tuple(user_ids)
        except TypeError as exc:
            raise CodingMemoryValidationError("user_ids must be iterable") from exc
        for user_id in users:
            require_identifier(user_id, "user_id")
        with self._operation():
            return self.store.corpus_state(users)

    def close(self) -> None:
        """关闭 SQLite 连接。."""
        try:
            with self._operation():
                self.store.close()
        finally:
            if self.vector_index is not None:
                self.vector_index.close()
