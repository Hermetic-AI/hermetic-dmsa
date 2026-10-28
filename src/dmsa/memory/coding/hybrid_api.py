"""CodingMemory 门面上的稠密混合检索接口.

门面方法只做参数转交, 具体实现在同层的 HybridService 里; 拆成独立模块
是为了让 CodingMemory 主文件留在项目规范的长度内.
"""

from __future__ import annotations

from dmsa.memory.coding.hybrid import HybridPolicy, HybridResult
from dmsa.memory.coding.hybrid_service import HybridService
from dmsa.memory.coding.models import SearchPolicy


class HybridApi:
    """CodingMemory 的稠密混合检索能力."""

    hybrid: HybridService

    def search_hybrid(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy | None = None,
        repo: str = "",
        hybrid: HybridPolicy | None = None,
    ) -> HybridResult:
        """按通道归一化融合词面与稠密召回, 再做一次有界重排.

        与 `search` 的区别在于两点: 稠密通道按余弦相似度而不是名次参与融合,
        融合之后按跨通道一致性与重复度做乘性重排. 结果里的 items 会给出每条
        证据的通道贡献、一致通道数与重排增量.
        """
        return self.hybrid.search(query, user_id, top_k, policy=policy, repo=repo, hybrid=hybrid)
