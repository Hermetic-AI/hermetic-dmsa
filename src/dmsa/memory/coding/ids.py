"""稳定的来源与切片 ID 计算."""

from __future__ import annotations

import hashlib


def message_id(user_id: str, request_id: str, ordinal: int) -> str:
    """产生包含 scope 的稳定来源 ID."""
    return hashlib.sha256("\0".join((user_id, request_id, str(ordinal))).encode()).hexdigest()


def chunk_id(message_id: str, start: int, end: int) -> str:
    """产生来源切片稳定 ID."""
    return hashlib.sha256("\0".join((message_id, str(start), str(end))).encode()).hexdigest()
