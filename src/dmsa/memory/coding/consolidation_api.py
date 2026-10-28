"""CodingMemory 门面上的记忆合并与跨仓库迁移接口.

门面方法只做参数转交, 具体实现在同层的 ConsolidationService 里; 拆成独立
模块是为了让 CodingMemory 主文件留在项目规范的长度内.
"""

from __future__ import annotations

from dmsa.memory.coding.consolidation_service import (
    ConsolidationReport,
    ConsolidationService,
    TransferReport,
)
from dmsa.memory.coding.consolidation_store import ConsolidationRecord, TransferRecord


class ConsolidationApi:
    """CodingMemory 的记忆合并与跨仓库迁移能力."""

    consolidations: ConsolidationService

    def consolidate(
        self, user_id: str, repo: str = "", *, min_group: int = 2, limit: int = 500
    ) -> ConsolidationReport:
        """把同一作用域里重复出现的经验合并成一条通用规则.

        合并出的条目保留全部来源 ID, 原始证据不被删除; 同一规则重复合并是
        幂等的, 报告里的 skipped 会指出已经合并过的规则.
        """
        return self.consolidations.consolidate(user_id, repo, min_group=min_group, limit=limit)

    def transfer(
        self, user_id: str, source_repo: str, target_repo: str, *, limit: int = 500
    ) -> TransferReport:
        """把可复用的通用规则从源仓库迁移到目标仓库.

        只迁移不含具体路径与符号的规则, 被拒绝的规则与泄漏项会在报告里列出.
        """
        return self.consolidations.transfer(user_id, source_repo, target_repo, limit=limit)

    def consolidation_records(self, user_id: str, repo: str = "") -> list[ConsolidationRecord]:
        """列出作用域内的合并记录及其来源."""
        return self.consolidations.consolidations(user_id, repo)

    def transfer_records(self, user_id: str, repo: str = "") -> list[TransferRecord]:
        """列出作用域作为目标时的迁移记录."""
        return self.consolidations.transfers(user_id, repo)
