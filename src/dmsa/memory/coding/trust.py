"""记忆条目的信任模型: 曝光、确认、反驳与时间衰减.

信任度只由可核验的观测驱动:

- 曝光(retrieved): 条目被检索返回过, 说明它至少与任务相关;
- 确认(confirmed): 反馈显式把该来源标为相关, 是最强的正向证据;
- 反驳(contradicted): 条目出现在失败轨迹的召回集合里, 且反馈判定召回不佳;
- 验证(verified): 任务成功时该条目仍在证据集中, 属于弱正向证据.

没有被观测到的条目保持中性, 只有明确的负面证据才会低于中性值. 信任度
不落库, 由观测计数在读取时推导, 避免缓存与事实不一致.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

BASE_TRUST = 0.5
CONFIRMED_DELTA = 0.15
CONTRADICTED_DELTA = 0.1
VERIFIED_DELTA = 0.05
MIN_TRUST = 0.02
MAX_TRUST = 1.0
DEFAULT_HALF_LIFE_DAYS = 30.0
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class MemoryStat:
    """单个记忆条目的观测统计."""

    chunk_id: str
    user_id: str
    retrieved: int = 0
    confirmed: int = 0
    contradicted: int = 0
    verified: int = 0
    evicted: bool = False
    first_revision: int = 0
    created_at: int = 0
    last_used: int | None = None
    updated_at: int = 0

    def to_dict(self, *, now: int | None = None) -> dict[str, Any]:
        """返回含当前信任度的可序列化视图."""
        moment = self.updated_at if now is None else now
        return {
            "chunk_id": self.chunk_id,
            "user_id": self.user_id,
            "retrieved": self.retrieved,
            "confirmed": self.confirmed,
            "contradicted": self.contradicted,
            "verified": self.verified,
            "evicted": self.evicted,
            "first_revision": self.first_revision,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "updated_at": self.updated_at,
            "trust": round(compute_trust(self, now=moment), 6),
        }


def base_trust(stat: MemoryStat) -> float:
    """按观测计数计算不做时间衰减的信任度."""
    raw = (
        BASE_TRUST
        + stat.confirmed * CONFIRMED_DELTA
        - stat.contradicted * CONTRADICTED_DELTA
        + stat.verified * VERIFIED_DELTA
    )
    return min(MAX_TRUST, max(MIN_TRUST, raw))


def compute_trust(
    stat: MemoryStat, *, now: int, half_life_days: float = DEFAULT_HALF_LIFE_DAYS
) -> float:
    """计算当前信任度: 观测计数决定基线, 距上次使用的时间做半衰期衰减."""
    if half_life_days <= 0:
        return base_trust(stat)
    anchor = stat.last_used if stat.last_used is not None else stat.created_at
    age_days = max(0.0, (now - anchor) / SECONDS_PER_DAY)
    decayed = base_trust(stat) * math.pow(0.5, age_days / half_life_days)
    return min(MAX_TRUST, max(MIN_TRUST, decayed))


def trust_multiplier(trust: float, weight: float) -> float:
    """把信任度转成检索调分系数; 中性条目系数为 1."""
    if weight <= 0:
        return 1.0
    span = 1.0 - BASE_TRUST
    bounded = min(MAX_TRUST, max(MIN_TRUST, trust))
    return 1.0 + weight * (bounded - BASE_TRUST) / span
