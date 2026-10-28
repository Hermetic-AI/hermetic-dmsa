"""稳定的来源与切片 ID 计算."""

from __future__ import annotations

import hashlib

from dmsa.memory.coding.models import CodingMemoryValidationError

# 复合 scope 键的分隔符; 出现在标识里会让子域归属产生歧义。
SCOPE_SEPARATOR = "#"


def message_id(user_id: str, request_id: str, ordinal: int) -> str:
    """产生包含 scope 的稳定来源 ID."""
    return hashlib.sha256("\0".join((user_id, request_id, str(ordinal))).encode()).hexdigest()


def chunk_id(message_id: str, start: int, end: int) -> str:
    """产生来源切片稳定 ID."""
    return hashlib.sha256("\0".join((message_id, str(start), str(end))).encode()).hexdigest()


def scope_key(user_id: str, repo: str = "") -> str:
    """返回 scope 键: 传入仓库时按仓库子域隔离证据.

    分隔符不允许出现在用户或仓库标识中, 否则 `user#repo` 无法反向归属,
    按用户清理数据时会漏删或误删相邻子域。
    """
    for field, value in (("user_id", user_id), ("repo", repo)):
        if SCOPE_SEPARATOR in value:
            raise CodingMemoryValidationError(f"{field} must not contain '{SCOPE_SEPARATOR}'")
    if not repo:
        return user_id
    return f"{user_id}{SCOPE_SEPARATOR}{repo}"
