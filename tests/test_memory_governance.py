"""记忆信任模型与写入门控的行为测试."""

from __future__ import annotations

from pathlib import Path

import pytest

from dmsa.memory.coding import CodingMemory
from dmsa.memory.coding.admission import jaccard, tokenize
from dmsa.memory.coding.models import CodingMemoryValidationError, SearchPolicy, SourceMessage
from dmsa.memory.coding.trust import (
    BASE_TRUST,
    MAX_TRUST,
    MIN_TRUST,
    MemoryStat,
    base_trust,
    compute_trust,
    trust_multiplier,
)

NOW = 1_700_000_000


def _memory(tmp_path: Path) -> CodingMemory:
    """构造本地 coding memory."""
    return CodingMemory(tmp_path / "trust.sqlite")


def _stat(**kwargs: int) -> MemoryStat:
    """构造统计快照."""
    base = {"chunk_id": "c1", "user_id": "u1", "created_at": NOW, "updated_at": NOW}
    return MemoryStat(**{**base, **kwargs})


def test_neutral_item_keeps_base_trust_and_observations_move_it() -> None:
    neutral = _stat()
    assert base_trust(neutral) == pytest.approx(BASE_TRUST)
    confirmed = _stat(confirmed=2)
    assert base_trust(confirmed) > BASE_TRUST
    contradicted = _stat(contradicted=3)
    assert base_trust(contradicted) < BASE_TRUST
    assert base_trust(_stat(confirmed=100)) <= MAX_TRUST
    assert base_trust(_stat(contradicted=100)) >= MIN_TRUST


def test_trust_decays_with_time_since_last_use() -> None:
    stat = _stat(last_used=NOW)
    fresh = compute_trust(stat, now=NOW)
    stale = compute_trust(stat, now=NOW + 30 * 86400)
    assert fresh > stale
    assert stale >= MIN_TRUST
    # 未被使用过的条目按创建时间衰减
    never_used = compute_trust(_stat(), now=NOW + 30 * 86400)
    assert never_used < BASE_TRUST


def test_trust_multiplier_is_neutral_at_base_and_bounded() -> None:
    assert trust_multiplier(BASE_TRUST, 0.1) == pytest.approx(1.0)
    assert trust_multiplier(MAX_TRUST, 0.1) > 1.0
    assert trust_multiplier(MIN_TRUST, 0.1) < 1.0
    assert trust_multiplier(MAX_TRUST, 0.0) == pytest.approx(1.0)


def test_tokenize_handles_ascii_and_chinese_and_jaccard() -> None:
    tokens = tokenize("缓存 revision 递增 cache")
    assert "cache" in tokens
    assert "缓存" in tokens
    assert jaccard(tokens, tokens) == pytest.approx(1.0)
    assert jaccard(frozenset(), tokens) == pytest.approx(0.0)
    assert 0.0 < jaccard(tokenize("缓存 revision"), tokenize("缓存 revision 递增")) < 1.0


def test_admission_rejects_near_duplicate_content(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add("r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")])
        result = memory.add_guarded(
            "r2", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")]
        )
        assert result.decision.admitted is False
        assert result.decision.reason == "redundant"
        assert result.decision.duplicate_of
        # 被门控拒绝时不落盘, revision 不变
        assert result.revision == 1
        assert len(memory.store.rows("u1")) == 1
    finally:
        memory.close()


def test_admission_admits_novel_content_and_reports_novelty(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add("r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")])
        result = memory.add_guarded(
            "r2", "u1", "s2", [SourceMessage("assistant", "连接池耗尽时先看超时配置再扩线程")]
        )
        assert result.decision.admitted is True
        assert result.decision.reason == "novel"
        assert result.decision.novelty > 0
        assert result.revision == 2
    finally:
        memory.close()


def test_admission_reports_idempotent_for_repeated_request(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        first = memory.add_guarded("r1", "u1", "s1", [SourceMessage("assistant", "唯一内容 alpha")])
        again = memory.add_guarded("r1", "u1", "s1", [SourceMessage("assistant", "唯一内容 alpha")])
        assert again.revision == first.revision
        assert again.decision.reason == "idempotent"
        assert again.decision.admitted is True
    finally:
        memory.close()


def test_admission_threshold_is_configurable(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        policy = SearchPolicy(admission_similarity=1.0)
        memory.add(
            "r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增")], policy=policy
        )
        result = memory.add_guarded(
            "r2",
            "u1",
            "s2",
            [SourceMessage("assistant", "缓存 revision 递增并补测试")],
            policy=policy,
        )
        assert result.decision.admitted is True
    finally:
        memory.close()


def test_admission_similarity_must_be_a_valid_ratio() -> None:
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(admission_similarity=1.5)
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(trust_weight=-0.1)


def test_exposure_and_verdict_accounting_shows_up_in_trust_report(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add("r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")])
        hits = memory.search("缓存", "u1", 5)
        assert hits
        memory.governance.record_exposure("u1", [hit.id for hit in hits])
        memory.governance.record_verdict("u1", [hits[0].id], confirmed=True)
        report = memory.governance.trust_report("u1")
        assert report
        entry = report[0]
        assert entry["retrieved"] == 1
        assert entry["confirmed"] == 1
        assert entry["trust"] > BASE_TRUST
    finally:
        memory.close()


def test_forget_evicts_low_trust_items_and_excludes_them_from_results(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add("r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")])
        hits = memory.search("缓存", "u1", 5)
        assert hits
        memory.governance.record_verdict("u1", [hit.id for hit in hits], contradicted=True)
        # 未被使用过的条目按创建时间衰减, 配合极低阈值即可淘汰
        evicted = memory.governance.forget("u1", floor=0.99, min_age_days=0)
        assert set(evicted) == {hit.id for hit in hits}
        assert memory.search("缓存", "u1", 5) == []
        memory.governance.restore("u1", evicted)
        assert memory.search("缓存", "u1", 5)
    finally:
        memory.close()


def test_trust_weight_changes_score_of_contradicted_item(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add("r1", "u1", "s1", [SourceMessage("assistant", "缓存 revision 递增的写法")])
        plain = memory.search("缓存", "u1", 5, SearchPolicy(trust_weight=0.0))
        assert plain
        memory.governance.record_verdict("u1", [plain[0].id], contradicted=True)
        weighted = memory.search("缓存", "u1", 5, SearchPolicy(trust_weight=0.2))
        assert weighted
        assert weighted[0].id == plain[0].id
        # 被判定无用的条目在信任调分后分数下降
        assert weighted[0].score < plain[0].score
        # 不启用信任调分时分数不受影响
        neutral = memory.search("缓存", "u1", 5, SearchPolicy(trust_weight=0.0))
        assert neutral[0].score == pytest.approx(plain[0].score)
    finally:
        memory.close()
