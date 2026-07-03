"""错误体系测试。"""

from __future__ import annotations

import pytest

from dmsa.errors import (
    DMSAError,
    EmbeddingFailedError,
    ErrorCode,
    FusionConflictError,
    InvalidConfigValueError,
    LineageNotFoundError,
    MemoryLayerError,
)


def test_dmsa_error_to_dict() -> None:
    """DMSAError.to_dict 序列化为标准 dict。"""
    err = LineageNotFoundError("node not found", details={"node_id": "abc"})
    result = err.to_dict()
    assert result["code"] == "DMSA-1002"
    assert result["message"] == "node not found"
    assert result["details"] == {"node_id": "abc"}


def test_dmsa_error_inherits_exception() -> None:
    """DMSAError 继承自 Exception。"""
    err = MemoryLayerError("test")
    assert isinstance(err, Exception)
    assert isinstance(err, DMSAError)


def test_dmsa_error_code_inherits_from_subclass() -> None:
    """子类的 code 属性覆盖基类。"""
    err1 = MemoryLayerError("a")
    err2 = EmbeddingFailedError("b")
    err3 = FusionConflictError("c")

    assert err1.code == ErrorCode.DMSA_1000
    assert err2.code == ErrorCode.DMSA_2001
    assert err3.code == ErrorCode.DMSA_2003


def test_dmsa_error_str_uses_message() -> None:
    """str(err) 使用 message。"""
    err = MemoryLayerError("hello world")
    assert str(err) == "hello world"


def test_dmsa_error_details_default_to_empty_dict() -> None:
    """未传 details 时默认为空 dict。"""
    err = MemoryLayerError("test")
    assert err.details == {}


def test_dmsa_error_can_be_raised_and_caught() -> None:
    """可以被 raise 并 except DMSAError 捕获。"""
    with pytest.raises(DMSAError) as exc_info:
        raise LineageNotFoundError("missing")
    assert exc_info.value.code == ErrorCode.DMSA_1002


def test_invalid_config_value_error_is_config_error() -> None:
    """InvalidConfigValueError 继承自 ConfigError（实际是 DMSAError 子类层级一致）。"""
    err = InvalidConfigValueError("bad")
    assert isinstance(err, DMSAError)
    assert err.code == ErrorCode.DMSA_6001


def test_dmsa_error_repr_includes_code_and_message() -> None:
    """repr 包含错误码和 message（便于日志）。"""
    err = MemoryLayerError("oops")
    r = repr(err)
    assert "MemoryLayerError" in r
    assert "DMSA-1000" in r
    assert "oops" in r
