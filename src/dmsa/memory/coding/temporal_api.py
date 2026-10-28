"""CodingMemory 门面上的时序 as-of 检索接口.

门面方法只做参数转交, 具体实现在同层的 TemporalService 里; 拆成独立模块
是为了让 CodingMemory 主文件留在项目规范的长度内.
"""

from __future__ import annotations

from dmsa.memory.coding.models import SearchPolicy
from dmsa.memory.coding.temporal import AsOf, TemporalResult
from dmsa.memory.coding.temporal_service import TemporalService


class TemporalApi:
    """CodingMemory 的时序回看能力."""

    temporal: TemporalService

    def search_as_of(
        self,
        query: str,
        user_id: str,
        top_k: int,
        as_of: AsOf,
        policy: SearchPolicy | None = None,
        repo: str = "",
    ) -> TemporalResult:
        """按写入序号或写入时刻回看历史证据.

        `as_of.revision` 是作用域内的写入序号, 适合复现"第 N 次写入时的记忆";
        `as_of.timestamp` 是条目写入时刻的 Unix 秒, 只有带统计行的条目才有该值.
        """
        return self.temporal.search(query, user_id, top_k, policy=policy, repo=repo, as_of=as_of)
