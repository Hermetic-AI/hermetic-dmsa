"""P0 预算计量、证据截断与通道排序回归测试。"""

from __future__ import annotations

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage, estimate_tokens
from dmsa.memory.coding.extraction import extract
from dmsa.memory.coding.packing import select_evidence


def test_tuned_defaults_support_platform_top_k() -> None:
    """默认策略必须能为 top_k=100 留出候选与预算."""
    policy = SearchPolicy()
    assert policy.candidate_limit == 240
    assert policy.per_session_limit == 16
    assert policy.max_chars == 120000
    assert policy.max_tokens == 32000
    assert policy.redundancy_threshold == 0.93


def test_default_policy_bounds_evidence_volume() -> None:
    """默认策略限制返回条数、分数下限、路径与最小证据量."""
    policy = SearchPolicy()
    assert policy.evidence_limit == 12
    assert policy.score_floor_ratio == 0.45
    assert policy.per_path_limit == 6
    assert policy.min_evidence == 4


def test_evidence_limit_caps_returned_hits(tmp_path) -> None:
    """top_k 只作上限, 实际返回受 evidence_limit 约束."""
    memory = CodingMemory(tmp_path / "limit.db")
    for index in range(20):
        memory.add(
            f"r{index}",
            "u",
            f"s{index}",
            [
                SourceMessage(
                    "assistant",
                    f"cache_refresh handler {index} def slugify(text): return text",
                )
            ],
        )
    hits = memory.search("cache_refresh slugify", "u", 100)
    assert len(hits) <= SearchPolicy().evidence_limit
    memory.close()


def test_score_floor_keeps_minimum_evidence() -> None:
    """分数断层时按最小证据量保留, 可配置为零只留最强证据."""
    policy = SearchPolicy(min_evidence=4, evidence_limit=12)
    ranked = [
        (f"id{index}", score, {"text": f"内容 {index}", "session_id": f"s{index}", "paths": ""})
        for index, score in enumerate((1.0, 0.9, 0.8, 0.7, 0.1, 0.05, 0.01, 0.0))
    ]
    kept = select_evidence(ranked, 100, policy)
    assert len(kept) == 4
    assert kept[0][0] == "id0"
    strict = select_evidence(ranked, 100, SearchPolicy(min_evidence=0, score_floor_ratio=0.85))
    assert [item[0] for item in strict] == ["id0", "id1"]


def test_per_path_limit_avoids_single_file_domination(tmp_path) -> None:
    """同一路径的切片数量受限, 避免单文件占满证据包."""
    memory = CodingMemory(tmp_path / "path.db")
    for index in range(8):
        memory.add(
            f"r{index}",
            "u",
            f"s{index}",
            [
                SourceMessage(
                    "assistant",
                    f"src/app.py 第 {index} 段 cache_refresh 逻辑 "
                    f"def handler_{index}(): return {index}",
                )
            ],
        )
    hits = memory.search("cache_refresh src/app.py", "u", 100)
    assert len(hits) <= SearchPolicy().per_path_limit
    memory.close()


def test_estimate_tokens_is_not_raw_utf8_bytes() -> None:
    """token 预算是字符/字节混合近似, 不再把字节数直接当 token."""
    ascii_text = "identifier " * 100
    cjk_text = "中文代码" * 100
    assert estimate_tokens("") == 0
    assert estimate_tokens(" ") == 1
    assert estimate_tokens(ascii_text) < len(ascii_text.encode("utf-8"))
    assert estimate_tokens(cjk_text) < len(cjk_text.encode("utf-8"))
    assert estimate_tokens(cjk_text) >= 300


def test_budget_truncates_tail_instead_of_dropping(tmp_path) -> None:
    """预算不足时保留正文前缀并标记截断, 只有连前缀都放不下才返回空."""
    memory = CodingMemory(tmp_path / "truncate.db")
    memory.add("r", "u", "s", [SourceMessage("user", "needle " + "code_line(); " * 200)])
    full = memory.search("needle", "u", 1)[0]
    tight = SearchPolicy(max_tokens=estimate_tokens(full.content) // 2)
    partial = memory.search("needle", "u", 1, tight)
    assert len(partial) == 1
    assert partial[0].content.endswith("[truncated]")
    assert len(partial[0].content) < len(full.content)
    assert memory.search("needle", "u", 1, SearchPolicy(max_chars=120)) == []
    memory.close()


def test_exact_channel_orders_by_match_count(tmp_path) -> None:
    """精确通道优先返回命中词项更多的切片, 而不是按 ID 任意取."""
    memory = CodingMemory(tmp_path / "exact.db")
    memory.add(
        "r",
        "u",
        "s",
        [
            SourceMessage("assistant", "alpha_value beta_gamma shared"),
            SourceMessage("assistant", "alpha_value only"),
        ],
    )
    policy = SearchPolicy(
        exact_weight=1, sparse_weight=0, experience_weight=0, candidate_limit=1
    )
    hits = memory.search("alpha_value beta_gamma", "u", 1, policy)
    assert len(hits) == 1
    assert "beta_gamma" in hits[0].content
    memory.close()


def test_substring_channel_orders_by_match_count(tmp_path) -> None:
    """短词项 LIKE 通道同样按命中数量排序."""
    memory = CodingMemory(tmp_path / "substring.db")
    memory.add(
        "r",
        "u",
        "s",
        [
            SourceMessage("assistant", "db io"),
            SourceMessage("assistant", "db only"),
        ],
    )
    policy = SearchPolicy(
        exact_weight=0, sparse_weight=1, experience_weight=0, candidate_limit=1
    )
    hits = memory.search("db io", "u", 1, policy)
    assert len(hits) == 1
    assert "io" in hits[0].content
    memory.close()


def test_symbol_extraction_keeps_code_identifiers_only() -> None:
    """普通英文单词不再进入符号索引, 定义名与代码风格标识符仍保留."""
    plain = extract((SourceMessage("assistant", "the quick brown fox jumps"),))[0]
    assert plain.symbols == ()
    code = extract(
        (
            SourceMessage(
                "assistant",
                "def slugify(text): return validate_user(text) + CacheLoader.name",
            ),
        )
    )[0]
    assert "slugify" in code.symbols
    assert "validate_user" in code.symbols
    assert "CacheLoader" in code.symbols
    assert "text" not in code.symbols
