"""持久向量的存储编解码.

向量按 float32 小端字节存放: 1024 维约 4KB, 相比 JSON 文本的约 14KB 明显更省,
而余弦相似度对 1e-7 量级的截断不敏感。历史数据里的 JSON 文本仍可读, 迁移不需要
重建语料。
"""

from __future__ import annotations

import hashlib
import json
from array import array
from collections.abc import Sequence


def text_hash(text: str) -> str:
    """按正文算稳定哈希, 作为内容寻址的向量复用键.

    同一段正文在同一模型下的向量必然相同, 因此哈希相同就可以直接复用已算结果,
    不必再次调用嵌入模型。128 位摘要足以避免碰撞。
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def encode_vector(vector: Sequence[float]) -> bytes:
    """把向量编码成 float32 小端字节."""
    return array("f", vector).tobytes()


def decode_vector(raw: object) -> tuple[float, ...]:
    """解码持久化的向量, 兼容历史 JSON 文本与当前 float32 字节."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        buffer = array("f")
        buffer.frombytes(bytes(raw))
        return tuple(buffer)
    return tuple(json.loads(str(raw)))
