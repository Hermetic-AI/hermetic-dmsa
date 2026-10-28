"""多跳迭代检索与召回解释的行为测试."""

from __future__ import annotations

import json

import pytest

from dmsa.memory import CodingMemory, CodingMemoryValidationError, SearchPolicy, SourceMessage

POLICY = SearchPolicy(neighbor_window=0)

# 首轮命中: 正文含查询词, 同时暴露一个文件路径与一个符号作为定位信号.
FIRST = (
    "根因: 缓存失效没有递增 revision。修复: 提交后递增 revision。"
    "改动在 src/cache.py 的 flush_buffer。"
)
# 次轮目标: 不含查询词, 但共享同一路径与符号, 只有扩展后才会被召回.
SECOND = "另一个调用点也要经过 flush_buffer, 见 src/cache.py, 注意空指针。"
# 无关证据: 既不含查询词, 也不共享定位信号.
NOISE = "顺手把日志格式统一成一行输出, 不影响逻辑。"


def _memory(tmp_path) -> CodingMemory:
    """构造含三条证据的 coding memory."""
    memory = CodingMemory(tmp_path / "recall.sqlite", policy=POLICY)
    memory.add("r1", "u", "s1", [SourceMessage("assistant", FIRST)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", SECOND)])
    memory.add("r3", "u", "s3", [SourceMessage("assistant", NOISE)])
    return memory


def test_explanation_reports_channel_terms_and_reason(tmp_path) -> None:
    """召回解释要给出命中通道、命中词项与一句人读说明."""
    memory = _memory(tmp_path)
    try:
        explanation = memory.search_explained("缓存 revision 修复", "u", 5)
        assert explanation.hops == 1
        assert len(explanation.rounds) == 1
        assert explanation.items
        item = explanation.items[0]
        assert item.channels
        assert "revision" in item.matched_terms
        assert "revision" in item.reason
        assert item.paths == ("src/cache.py",)
    finally:
        memory.close()


def test_explanation_is_json_serializable(tmp_path) -> None:
    """解释结果可直接落盘或回传客户端."""
    memory = _memory(tmp_path)
    try:
        payload = memory.search_explained("缓存 revision 修复", "u", 5, hops=2).to_dict()
        assert json.loads(json.dumps(payload, ensure_ascii=False))["hops"] == 2
    finally:
        memory.close()


def test_multi_hop_finds_evidence_beyond_first_round(tmp_path) -> None:
    """多跳用首轮的定位信号召回同一处代码的其它证据."""
    memory = _memory(tmp_path)
    try:
        single = memory.search_explained("缓存 revision 修复", "u", 5, hops=1)
        multi = memory.search_explained("缓存 revision 修复", "u", 5, hops=2)
        assert len(multi.rounds) == 2
        assert "src/cache.py" in multi.rounds[1].added_terms
        assert "flush_buffer" in multi.rounds[1].added_terms
        single_ids = {item.id for item in single.items}
        multi_ids = {item.id for item in multi.items}
        assert multi_ids > single_ids
    finally:
        memory.close()


def test_multi_hop_keeps_first_round_results(tmp_path) -> None:
    """扩展只增加候选, 不会丢掉首轮已经召回的证据."""
    memory = _memory(tmp_path)
    try:
        single = memory.search_explained("缓存 revision 修复", "u", 5, hops=1)
        multi = memory.search_explained("缓存 revision 修复", "u", 5, hops=2)
        assert {item.id for item in single.items} <= {item.id for item in multi.items}
    finally:
        memory.close()


def test_multi_hop_stops_when_no_new_terms(tmp_path) -> None:
    """没有新扩展词时提前收手, 不做无意义的额外召回."""
    memory = _memory(tmp_path)
    try:
        explanation = memory.search_explained("缓存 revision 修复", "u", 5, hops=3)
        assert explanation.hops <= 3
        assert all(round_.added_terms for round_ in explanation.rounds[1:])
    finally:
        memory.close()


def test_explanation_items_align_with_plain_search(tmp_path) -> None:
    """解释里的证据 ID 与普通检索保持一致."""
    memory = _memory(tmp_path)
    try:
        hits = memory.search("缓存 revision 修复", "u", 5)
        explanation = memory.search_explained("缓存 revision 修复", "u", 5)
        assert {hit.id for hit in hits} == {item.id for item in explanation.items}
    finally:
        memory.close()


def test_invalid_hops_is_rejected(tmp_path) -> None:
    """跳数越界必须报错, 而不是静默截断."""
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.search_explained("缓存", "u", 5, hops=9)
        with pytest.raises(CodingMemoryValidationError):
            memory.search_explained("缓存", "u", 5, hops=0)
    finally:
        memory.close()


def test_invalid_top_k_is_rejected(tmp_path) -> None:
    """top_k 必须是有界正整数."""
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.search_explained("缓存", "u", 0)
        with pytest.raises(CodingMemoryValidationError):
            memory.search_explained("缓存", "u", 5, hops=True)
    finally:
        memory.close()


def test_empty_scope_reports_no_items(tmp_path) -> None:
    """作用域为空时如实返回空结果, 不编造召回."""
    memory = _memory(tmp_path)
    try:
        explanation = memory.search_explained("缓存", "empty", 5)
        assert explanation.items == ()
        assert explanation.rounds[0].candidates == 0
    finally:
        memory.close()
