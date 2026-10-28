"""持久向量编解码的存储成本与兼容性测试."""

from __future__ import annotations

import json

from dmsa.memory.coding.vector_codec import decode_vector, encode_vector


def test_float32_encoding_is_much_smaller_than_json() -> None:
    """1024 维向量的字节存放必须明显小于 JSON 文本."""
    vector = tuple(index / 1024 for index in range(1024))
    packed = encode_vector(vector)
    assert len(packed) == 1024 * 4
    assert len(packed) < len(json.dumps(vector).encode("utf-8")) / 2


def test_round_trip_keeps_values_within_float32_precision() -> None:
    """编解码往返后数值仍在 float32 精度内."""
    vector = (0.123456789, -0.987654321, 0.0, 1.0)
    restored = decode_vector(encode_vector(vector))
    assert len(restored) == len(vector)
    for original, value in zip(vector, restored, strict=True):
        assert abs(original - value) < 1e-6


def test_decode_reads_legacy_json_text() -> None:
    """历史 JSON 文本仍可读, 迁移不需要重建语料."""
    vector = (0.5, -0.25, 0.125)
    assert decode_vector(json.dumps(vector)) == vector


def test_decode_handles_bytes_column_value() -> None:
    """SQLite 字节列读回的是 bytes, 必须走字节分支."""
    vector = (0.5, -0.25, 0.125)
    assert decode_vector(encode_vector(vector)) == vector
