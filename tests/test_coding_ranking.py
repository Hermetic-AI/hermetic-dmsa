"""证据质量排序与查询标识符拆分的回归测试。"""

from __future__ import annotations

from dmsa.memory import CodingMemory, SourceMessage
from dmsa.memory.coding.ranking import (
    evidence_adjustment,
    is_stale_write,
    query_features,
)
from dmsa.memory.coding.tokenize import query_terms

_DOCSTRING_BODY = '    """Test copying a time series."""'
_ERROR_BODY = (
    "def _check_required_columns(self):\n"
    "    if self.required_columns is None:\n"
    '        raise ValueError("object is invalid")\n'
)
_STALE_WRITE_BODY = (
    '[tool_use Write] {"file_path": "src/core.py", "content": '
    '"class TimeSeries:\\n    def copy(self): ..."}'
)


def test_query_terms_split_identifier_fragments() -> None:
    """蛇形、点分和驼峰标识符都要拆成三元组通道可命中的片段."""
    terms = query_terms("missing_required_columns")
    assert "missing" in terms
    assert "required_columns" in terms
    dotted = query_terms("TimeSeries._check_required_columns")
    assert "time_series" in dotted
    assert "required_columns" in dotted
    assert query_terms("invalid") == ("invalid",)


def test_query_features_capture_phrases_identifiers_and_cjk() -> None:
    """查询特征同时包含英文短语、标识符片段和中文三元组."""
    features = query_features("修复 _check_required_columns 列检查失败")
    assert "check_required" in features.identifiers
    assert "required_columns" in features.identifiers
    assert "列检查" in features.cjk_grams
    assert all(" " in phrase for phrase in features.phrases)


def test_query_features_extract_paths_but_not_method_names() -> None:
    """查询中的文件线索用于匹配补丁路径, 方法名不能被误判成文件."""
    features = query_features("Core.py 的 TimeSeries._check_required_columns 报错")
    assert features.paths == frozenset({"core.py"})


def test_evidence_adjustment_prefers_verified_patch_over_padded_attempt() -> None:
    """带验证的成功补丁应高于重复填充的历史失败尝试."""
    features = query_features("ops_0_clamp.py 的 clamp_0 边界错误")
    verified = (
        "最终修复如下:\n"
        "```python path=ops_0_clamp.py\n"
        "def clamp_0(value, lower, upper):\n"
        "    return max(lower, min(upper, value))\n"
        "```\n验证: pytest 全部通过。"
    )
    padded_attempt = (
        "历史修复尝试 1:\n"
        "```python path=ops_0_clamp.py\n"
        "def clamp_0(value, lower, upper):\n"
        "    return value\n"
        "```\n补充记录:" + "重复填充" * 80
    )
    assert evidence_adjustment(features, verified) > evidence_adjustment(
        features, padded_attempt
    )


def test_stale_write_detection_tolerates_leading_space() -> None:
    """历史整文件写入带前导空白时仍应识别为过期快照."""
    assert is_stale_write(_STALE_WRITE_BODY)
    assert is_stale_write("  " + _STALE_WRITE_BODY)
    assert not is_stale_write(_ERROR_BODY)


def test_evidence_adjustment_prefers_code_over_plain_docstring() -> None:
    """带错误分支的实现应高于纯文档段落, 过期写入应被压低."""
    features = query_features("required columns raise ValueError")
    error_score = evidence_adjustment(
        features, _ERROR_BODY, "src/core.py", "_check_required_columns"
    )
    docstring_score = evidence_adjustment(features, _DOCSTRING_BODY)
    stale_score = evidence_adjustment(features, _STALE_WRITE_BODY)
    assert error_score > docstring_score
    assert stale_score < 0


def test_search_ranks_error_function_first_and_drops_stale_write(tmp_path) -> None:
    """真实检索里目标函数应排第一, 过期整文件写入不进入证据包."""
    memory = CodingMemory(tmp_path / "ranking.db")
    memory.add(
        "r",
        "u",
        "s",
        [
            SourceMessage("assistant", _DOCSTRING_BODY),
            SourceMessage("assistant", _STALE_WRITE_BODY),
            SourceMessage("assistant", _ERROR_BODY),
        ],
    )
    hits = memory.search("TimeSeries required columns raise ValueError", "u", 3)
    assert hits
    assert "_check_required_columns" in hits[0].content
    assert all("[tool_use Write]" not in hit.content for hit in hits)
    memory.close()


def test_search_drops_stale_write_even_when_it_is_the_only_match(tmp_path) -> None:
    """宁可不注入证据, 也不把过期整文件快照送回当前仓库."""
    memory = CodingMemory(tmp_path / "only.db")
    memory.add("r", "u", "s", [SourceMessage("assistant", _STALE_WRITE_BODY)])
    assert memory.search("TimeSeries copy", "u", 3) == []
    memory.close()
