"""记忆合并与跨仓库迁移服务.

合并把同一作用域里反复出现的同一条经验收敛成一条通用规则, 派生条目保留
全部来源 ID; 迁移把不依赖具体路径与符号的规则复制到另一个仓库的子域, 让
在 A 仓库学到的做法能在 B 仓库被检索到. 两者都不删除原始证据, 只新增派生
条目, 因此任何结论都能回溯到最初的那次记录.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.code_anchors import (
    CONFIDENCE,
    KIND_MENTIONS,
    CodeAnchor,
)
from dmsa.memory.coding.consolidation_store import (
    CONSOLIDATED_ROLE,
    TRANSFERRED_ROLE,
    ConsolidationRecord,
    TransferRecord,
    insert_consolidation,
    insert_transfer,
    list_consolidations,
    list_transfers,
    publish_consolidated,
    scope_slices,
)
from dmsa.memory.coding.generalize import RuleGroup, group_rules
from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    SearchPolicy,
    require_identifier,
    require_optional_identifier,
)
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore

# 单次合并或迁移最多处理的规则组数, 避免一次调用写出过多派生条目.
MAX_GROUPS = 50


@dataclass(frozen=True)
class ConsolidationReport:
    """一次合并的结果: 命中的规则组、写入的派生条目与被跳过的规则."""

    scope: str
    groups: tuple[RuleGroup, ...]
    written: tuple[str, ...]
    skipped: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的合并报告."""
        return {
            "scope": self.scope,
            "groups": [group.to_dict() for group in self.groups],
            "written": list(self.written),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class TransferReport:
    """一次跨仓库迁移的结果: 迁出的规则与被拒的规则."""

    source_scope: str
    target_scope: str
    transferred: tuple[tuple[str, str], ...]
    skipped: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的迁移报告."""
        return {
            "source_scope": self.source_scope,
            "target_scope": self.target_scope,
            "transferred": [
                {"rule_key": key, "chunk_id": chunk} for key, chunk in self.transferred
            ],
            "skipped": [{"rule_key": key, "reason": reason} for key, reason in self.skipped],
        }


def _mention_anchors(group: RuleGroup) -> tuple[CodeAnchor, ...]:
    """把规则组里的路径与符号转成「提及」型锚点, 让派生条目仍可被定位召回."""
    anchors: list[CodeAnchor] = []
    for path in group.paths:
        anchors.append(CodeAnchor(path, "", KIND_MENTIONS, CONFIDENCE[KIND_MENTIONS]))
    for symbol in group.symbols:
        anchors.append(CodeAnchor("", symbol, KIND_MENTIONS, CONFIDENCE[KIND_MENTIONS]))
    return tuple(anchors)


class ConsolidationService:
    """在作用域之间合并重复经验并迁移可复用的通用规则."""

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

    def consolidate(
        self, user_id: str, repo: str = "", *, min_group: int = 2, limit: int = 500
    ) -> ConsolidationReport:
        """把同一作用域里重复出现的经验合并成一条通用规则.

        合并出的条目保留来源作用域内的具体路径与符号, 因此在本仓库里仍可被
        定位召回; 它额外登记全部来源 ID, 保证规则来自哪些记录一目了然.
        """
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(min_group) is not int or min_group < 2:
            raise CodingMemoryValidationError("min_group must be an integer of at least 2")
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise CodingMemoryValidationError("limit must be an integer between 1 and 2000")
        scope = scope_key(user_id, repo)
        with self._operation():
            slices = scope_slices(self.store.db, scope, limit)
        groups = [group for group in group_rules(slices) if len(group.source_ids) >= min_group]
        candidates = groups[:MAX_GROUPS]
        written: list[str] = []
        skipped: list[str] = []
        for group in candidates:
            chunk_key = self._publish(
                scope,
                scope,
                self._request_id("consolidation", scope, group.rule_key),
                f"consolidation:{group.rule_key}",
                CONSOLIDATED_ROLE,
                group,
                keep_anchors=True,
            )
            if chunk_key is None:
                skipped.append(group.rule_key)
                continue
            written.append(chunk_key)
        return ConsolidationReport(scope, tuple(candidates), tuple(written), tuple(skipped))

    def transfer(
        self, user_id: str, source_repo: str, target_repo: str, *, limit: int = 500
    ) -> TransferReport:
        """把源作用域里可迁移的通用规则复制到目标作用域.

        迁移只带走抽象后的规则正文: 具体路径与符号在目标仓库指向不同的代码,
        留在那里只会误导定位. 仍带仓库专有标识的规则被拒绝, 并在报告里列出
        具体泄漏项, 便于人工判断这条经验能不能手写迁移.
        """
        require_identifier(user_id, "user_id")
        require_optional_identifier(source_repo, "source_repo")
        require_optional_identifier(target_repo, "target_repo")
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise CodingMemoryValidationError("limit must be an integer between 1 and 2000")
        if source_repo == target_repo:
            raise CodingMemoryValidationError("source_repo and target_repo must differ")
        source_scope = scope_key(user_id, source_repo)
        target_scope = scope_key(user_id, target_repo)
        with self._operation():
            slices = scope_slices(self.store.db, source_scope, limit)
        transferred: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []
        for group in group_rules(slices)[:MAX_GROUPS]:
            if not group.transferable:
                skipped.append((group.rule_key, "repo_specific:" + ",".join(group.leaked)))
                continue
            chunk_key = self._publish(
                target_scope,
                source_scope,
                self._request_id("transfer", source_scope, group.rule_key),
                f"transfer:{group.rule_key}",
                TRANSFERRED_ROLE,
                group,
                keep_anchors=False,
            )
            if chunk_key is None:
                skipped.append((group.rule_key, "already_transferred"))
                continue
            transferred.append((group.rule_key, chunk_key))
        return TransferReport(source_scope, target_scope, tuple(transferred), tuple(skipped))

    def consolidations(self, user_id: str, repo: str = "") -> list[ConsolidationRecord]:
        """列出作用域内的合并记录."""
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        scope = scope_key(user_id, repo)
        with self._operation():
            return list_consolidations(self.store.db, scope)

    def transfers(self, user_id: str, repo: str = "") -> list[TransferRecord]:
        """列出作用域作为目标时的迁移记录."""
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        scope = scope_key(user_id, repo)
        with self._operation():
            return list_transfers(self.store.db, scope)

    def _publish(
        self,
        write_scope: str,
        origin_scope: str,
        request_id: str,
        session_id: str,
        role: str,
        group: RuleGroup,
        *,
        keep_anchors: bool,
    ) -> str | None:
        """在单个事务里写入派生条目并登记来源追踪.

        返回 None 表示同一规则此前已经写入, 调用方把它当作幂等跳过.
        """
        paths = group.paths if keep_anchors else ()
        symbols = group.symbols if keep_anchors else ()
        anchors = _mention_anchors(group) if keep_anchors else ()
        vector = self._embed(group.rule)
        with self.store.lock, self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            result = publish_consolidated(
                self.store.db,
                write_scope,
                request_id,
                session_id,
                role,
                group.rule,
                paths,
                symbols,
                anchors,
                vector,
                self.retriever.fingerprint,
            )
            if result is None:
                return None
            chunk_key, _revision = result
            if role == CONSOLIDATED_ROLE:
                insert_consolidation(self.store.db, write_scope, chunk_key, group)
            else:
                insert_transfer(
                    self.store.db,
                    origin_scope,
                    write_scope,
                    chunk_key,
                    group.rule_key,
                    group.source_ids,
                )
            return chunk_key

    def _embed(self, text: str) -> tuple[float, ...] | None:
        """计算派生条目的向量; 没有嵌入器时返回 None."""
        vectors = self.retriever.embed_new([text])
        return vectors[0] if vectors else None

    @staticmethod
    def _request_id(prefix: str, origin_scope: str, rule_key: str) -> str:
        """返回派生写入使用的稳定幂等键.

        键里带上来源作用域, 因此同一条规则从两个仓库迁到同一个仓库时会各自
        留一条记录, 不会被后一次迁移当成重复而丢掉来源.
        """
        digest = hashlib.sha256("\0".join((origin_scope, rule_key)).encode()).hexdigest()
        return f"{prefix}:{digest}"
