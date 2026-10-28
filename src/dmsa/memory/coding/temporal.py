"""时序 as-of 查询的坐标定义与 SQL 过滤.

两种坐标互补: revision 是作用域内的写入序号, 每次成功写入递增, 历史完整可用,
适合回答"回到第 N 次写入时的记忆长什么样"; timestamp 是条目写入时刻的 Unix 秒,
适合回答"上周的证据现在是什么样", 但只有带统计行的条目才有该值.

过滤必须在 SQL 层完成: 候选限额是按通道截断的, 如果先召回再过滤, 较新的证据
会先把名额占满, 较旧的目标证据反而被挤出候选集.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.models import CodingMemoryValidationError, SearchHit


@dataclass(frozen=True)
class AsOf:
    """时序查询坐标: 写入序号与写入时刻两种口径.

    revision 口径走 requests 表, 每个请求写入时都记下当时的 scope revision,
    因此历史完整; timestamp 口径走 memory_stats 表, 只有带统计行的条目才有写入
    时刻, 统计表建立之前写入的条目不在时间戳口径的可见范围内.
    """

    revision: int | None = None
    timestamp: int | None = None

    def __post_init__(self) -> None:
        """拒绝空坐标与非法取值."""
        if self.revision is None and self.timestamp is None:
            raise CodingMemoryValidationError("as_of requires revision or timestamp")
        for name, value in (("revision", self.revision), ("timestamp", self.timestamp)):
            if value is None:
                continue
            if type(value) is not int or value < 0:
                raise CodingMemoryValidationError(f"as_of {name} must be a nonnegative integer")

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的坐标."""
        return {"revision": self.revision, "timestamp": self.timestamp}


@dataclass(frozen=True)
class TemporalResult:
    """一次时序检索的证据、查询坐标与作用域当前写入序号."""

    query: str
    hits: tuple[SearchHit, ...]
    as_of: AsOf
    scope_revision: int

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的时序检索视图."""
        return {
            "query": self.query,
            "as_of": self.as_of.to_dict(),
            "scope_revision": self.scope_revision,
            "hits": [
                {"id": hit.id, "content": hit.content, "score": round(hit.score, 6)}
                for hit in self.hits
            ],
        }


def as_of_filter(alias: str, as_of: AsOf | None) -> tuple[str, tuple[int, ...]]:
    """按时序坐标生成 SQL 过滤片段与绑定参数.

    返回的片段以 " AND " 开头, 可直接拼进 WHERE 子句; as_of 为空时返回空片段,
    调用方无需分支即可保持既有行为不变.
    """
    if as_of is None:
        return "", ()
    clauses: list[str] = []
    params: list[int] = []
    if as_of.revision is not None:
        clauses.append(
            f"EXISTS (SELECT 1 FROM requests r WHERE r.user_id={alias}.user_id "
            f"AND r.request_id={alias}.request_id AND r.revision<=?)"
        )
        params.append(as_of.revision)
    if as_of.timestamp is not None:
        clauses.append(
            f"EXISTS (SELECT 1 FROM memory_stats m WHERE m.user_id={alias}.user_id "
            f"AND m.chunk_id={alias}.id AND m.created_at<=?)"
        )
        params.append(as_of.timestamp)
    if not clauses:
        return "", ()
    return " AND " + " AND ".join(clauses), tuple(params)
