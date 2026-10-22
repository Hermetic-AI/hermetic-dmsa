"""Coding memory 的持久化与检索保证。"""

from __future__ import annotations

import pytest

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.extraction import extract
from dmsa.memory.coding.models import (
    CodingMemoryConflictError,
    CodingMemoryError,
    CodingMemoryValidationError,
    DenseScopeTooLargeError,
)


def test_scope_isolation_and_source_spans(tmp_path) -> None:
    """检索仅返回本 scope 且证据保留来源偏移。"""
    memory = CodingMemory(tmp_path / "memory.db")
    memory.add("r1", "alice", "s1", [SourceMessage("assistant", "fix parse_config in src/app.py")])
    memory.add("r2", "bob", "s2", [SourceMessage("assistant", "fix parse_config in src/secret.py")])
    hits = memory.search("parse_config", "alice", 5)
    assert len(hits) == 1
    assert "src/app.py" in hits[0].content
    assert "chars=0:" in hits[0].content
    assert memory.search("secret.py", "alice", 5) == []
    memory.close()


def test_sparse_only_search_finds_original_evidence(tmp_path) -> None:
    """停用 exact 通道时, FTS 仍能找到原始消息。"""
    memory = CodingMemory(tmp_path / "memory.db")
    memory.add("r", "u", "s", [SourceMessage("assistant", "intermittent cache refresh issue")])
    hits = memory.search("intermittent refresh", "u", 3, SearchPolicy(exact_weight=0))
    assert len(hits) == 1
    assert "cache refresh issue" in hits[0].content
    memory.close()


def test_experience_weight_can_disable_card_reranking() -> None:
    """评测可以把原始混合检索与经验卡完整策略分开对照。"""
    assert SearchPolicy(experience_weight=0).experience_weight == 0
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(experience_weight=11)


def test_idempotency_conflict_and_restart(tmp_path) -> None:
    """相同来源重试稳定, 冲突拒绝, 重开后保留数据。"""
    path = tmp_path / "memory.db"
    memory = CodingMemory(path)
    source = [SourceMessage("user", "ValueError in load_data")]
    assert memory.add("r", "u", "s", source) == 1
    assert memory.add("r", "u", "s", source) == 1
    with pytest.raises(CodingMemoryConflictError):
        memory.add("r", "u", "s", [SourceMessage("user", "different")])
    memory.close()
    reopened = CodingMemory(path)
    assert "ValueError" in reopened.search("ValueError", "u", 2)[0].content
    reopened.close()


def test_outcome_negative_example_and_long_content(tmp_path) -> None:
    """不推断未知结果, 保留失败标签与长轨迹尾部。"""
    memory = CodingMemory(tmp_path / "memory.db")
    text = "x" * 4000 + "\nFAILED test_parser\n" + "tail_symbol" * 100
    memory.add("r", "u", "s", [SourceMessage("tool", text)])
    rows = memory.store.rows("u")
    assert len(rows) >= 3
    assert any(row["outcome"] == "observed_failure" for row in rows)
    assert all("tail_symbol" in row["text"] for row in rows if row["end"] > 3800)
    memory.add("other", "u", "s", [SourceMessage("assistant", "maybe fixed")])
    assert any(row["outcome"] == "unknown" for row in memory.store.rows("u"))
    memory.close()


def test_exact_terms_deduplicate_after_case_normalization(tmp_path) -> None:
    """大小写不同的同词项写入单条精确索引记录。"""
    memory = CodingMemory(tmp_path / "case-terms.db")
    memory.add("r", "u", "s", [SourceMessage("user", "Foo foo")])
    assert memory.search("foo", "u", 2)
    count = memory.store.db.execute(
        "SELECT count(*) FROM exact_terms WHERE term='foo'"
    ).fetchone()[0]
    assert count == 1
    memory.close()


def test_corpus_fingerprint_distinguishes_databases_and_survives_reopen(tmp_path) -> None:
    """来源版本相同的数据库仍有独立指纹, 重开后指纹稳定。"""
    first_path, second_path = tmp_path / "first.db", tmp_path / "second.db"
    first = CodingMemory(first_path, None)
    second = CodingMemory(second_path, None)
    first.add("r", "u", "s", [SourceMessage("user", "source one")])
    second.add("r", "u", "s", [SourceMessage("user", "source two")])
    first_fingerprint = first.corpus_fingerprint(["u"])
    assert first_fingerprint != second.corpus_fingerprint(["u"])
    first.close()
    second.close()
    reopened = CodingMemory(first_path, None)
    assert reopened.corpus_fingerprint(["u"]) == first_fingerprint
    reopened.close()
    reopened = CodingMemory(first_path, None)
    assert reopened.corpus_fingerprint(["u"]) == first_fingerprint
    reopened.close()


def test_corpus_fingerprint_tracks_other_scope_mutations(tmp_path) -> None:
    """全局 FTS 统计可能改变其他 scope 排序, 因此指纹包含全局 generation。"""
    memory = CodingMemory(tmp_path / "global-generation.db", None)
    memory.add("alice-1", "alice", "s", [SourceMessage("user", "alpha evidence")])
    before = memory.corpus_fingerprint(["alice"])
    memory.add("bob-1", "bob", "s", [SourceMessage("user", "unrelated corpus")])
    assert memory.corpus_fingerprint(["alice"]) != before
    memory.close()


def test_independent_writers_serialize_revision_assignment(tmp_path) -> None:
    """独立连接并发写入仍获得连续 revision, 同 key 重试幂等。"""
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "writers.db"
    first, second = CodingMemory(path, None), CodingMemory(path, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(
            lambda args: args[0].add(args[1], "u", "s", [SourceMessage("user", args[1])]),
            [(first, "a"), (second, "b")],
        ))
    assert sorted(revisions) == [1, 2]
    assert first.add("a", "u", "s", [SourceMessage("user", "a")]) == 1
    assert first.store.revision("u") == 2
    first.close()
    second.close()


@pytest.mark.parametrize("field", ["request", "user", "session"])
def test_identifiers_reject_nul_delimiter(field: str, tmp_path) -> None:
    """稳定 ID 分隔符不允许出现在来源标识字段。"""
    memory = CodingMemory(tmp_path / "nul.db", None)
    values = {"request_id": "r", "user_id": "u", "session_id": "s"}
    values[{"request": "request_id", "user": "user_id", "session": "session_id"}[field]] = "a\0b"
    with pytest.raises(CodingMemoryValidationError, match="NUL"):
        memory.add(**values, messages=[SourceMessage("user", "safe content")])
    memory.close()


def test_negated_test_success_remains_unknown() -> None:
    """否定测试通过的描述不能标为成功。"""
    chunks = extract((SourceMessage("tool", "tests not passed"),))
    assert chunks[0].outcome == "unknown"


def test_top_k_policy_override_and_delete(tmp_path) -> None:
    """边界参数有明确错误, 临时策略不修改实例, 删除彻底清空。"""
    memory = CodingMemory(tmp_path / "memory.db")
    memory.add("r", "u", "s", [SourceMessage("assistant", "AssertionError in validate_user")])
    before_delete = memory.corpus_fingerprint(["u"])
    original = memory.policy
    with pytest.raises(CodingMemoryValidationError):
        memory.search("", "u", 1)
    with pytest.raises(CodingMemoryValidationError):
        memory.search("validate_user", "u", 0)
    memory.search("validate_user", "u", 1, SearchPolicy(max_chars=100))
    assert memory.policy is original
    memory.delete_scope("u")
    assert memory.search("validate_user", "u", 1) == []
    assert memory.corpus_fingerprint(["u"]) != before_delete
    memory.close()


def test_transaction_rolls_back_partial_request(tmp_path) -> None:
    """消息插入失败时 request、chunks 和 revision 全部回滚。"""
    memory = CodingMemory(tmp_path / "memory.db")
    memory.store.db.execute(
        "CREATE TRIGGER reject_message BEFORE INSERT ON messages "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(CodingMemoryError, match="database operation"):
        memory.add("r", "u", "s", [SourceMessage("user", "evidence")])
    memory.store.db.execute("DROP TRIGGER reject_message")
    assert memory.store.revision("u") == 0
    assert memory.store.rows("u") == []
    memory.close()


class _Embedder:
    """測試用 deterministic dense backend。"""

    fingerprint = "unit-v1"
    dimension = 2

    def embed_batch(self, texts):
        """返回有效的固定向量。"""
        return [[1.0, 0.5] for _ in texts]


def test_dense_validation_restart_and_scope_limit(tmp_path) -> None:
    """dense 校验拒绝坏向量, 重启明确无向量并拒绝超限扫描。"""
    memory = CodingMemory(tmp_path / "memory.db", embedder=_Embedder())
    memory.add("r", "u", "s", [SourceMessage("user", "dense evidence")])
    assert memory.search("dense", "u", 1)
    memory.close()
    reopened = CodingMemory(tmp_path / "memory.db", embedder=_Embedder())
    assert reopened.search("dense", "u", 1)  # sparse remains usable after restart
    reopened.add("r2", "u", "s", [SourceMessage("user", "second dense evidence")])
    reopened.policy = SearchPolicy(dense_scan_limit=1)
    with pytest.raises(DenseScopeTooLargeError):
        reopened.search("dense", "u", 1)
    reopened.close()


def test_invalid_embedder_vectors_are_rejected(tmp_path) -> None:
    """零向量或错误维度不能被静默接受。"""

    class BadEmbedder(_Embedder):
        def embed_batch(self, texts):
            """返回无效零向量。"""
            return [[0.0, 0.0] for _ in texts]

    memory = CodingMemory(tmp_path / "memory.db", embedder=BadEmbedder())
    with pytest.raises(CodingMemoryValidationError):
        memory.add("r", "u", "s", [SourceMessage("user", "evidence")])
    assert memory.store.revision("u") == 0
    memory.close()


def test_dense_durable_cross_instance_and_model_isolation(tmp_path) -> None:
    """纯 dense 检索可跨实例重开, 不混用其他模型向量。"""
    path = tmp_path / "memory.db"
    dense = SearchPolicy(exact_weight=0, sparse_weight=0)
    writer = CodingMemory(path, embedder=_Embedder(), policy=dense)
    reader = CodingMemory(path, embedder=_Embedder(), policy=dense)
    writer.add("r", "u", "s", [SourceMessage("tool", "durable evidence")])
    assert reader.search("unrelated semantic query", "u", 1)
    writer.close()
    reader.close()
    reopened = CodingMemory(path, embedder=_Embedder(), policy=dense)
    assert reopened.search("unrelated semantic query", "u", 1)
    reopened.close()

    class OtherModel(_Embedder):
        fingerprint = "different-model"

    other = CodingMemory(path, embedder=OtherModel(), policy=dense)
    assert other.search("durable evidence", "u", 1) == []
    other.close()


def test_retry_skips_embedding_and_epoch_survives_delete(tmp_path) -> None:
    """已提交重试不再调用模型, 删除重加不能恢复旧指纹。"""
    embedder = _Embedder()
    memory = CodingMemory(tmp_path / "memory.db", embedder=embedder)
    messages = [SourceMessage("tool", "evidence", timestamp=1234)]
    revision = memory.add("r", "u", "s", messages)
    before = memory.corpus_state(["u"])

    def fail(texts):
        raise AssertionError("retry must not embed")

    embedder.embed_batch = fail
    assert memory.add("r", "u", "s", messages) == revision
    memory.delete_scope("u")
    embedder.embed_batch = _Embedder().embed_batch
    assert memory.add("r", "u", "s", messages) > before.revision
    assert memory.corpus_state(["u"]).fingerprint != before.fingerprint
    memory.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_policy_weights(value) -> None:
    """策略拒绝非有限数字与布尔权重。"""
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(dense_weight=value)


def test_sparse_database_errors_are_not_silent_and_punctuation_is_safe(tmp_path) -> None:
    """FTS 结构故障以记忆层异常报告, 标点查询仍正常返回空结果。"""
    memory = CodingMemory(tmp_path / "memory.db")
    assert memory.search('"*()', "u", 2) == []
    memory.store.db.execute("DROP TABLE chunks")
    with pytest.raises(CodingMemoryError, match="sparse retrieval"):
        memory.store.sparse_candidates("evidence", "u", 2)
    memory.close()


@pytest.mark.parametrize(
    "text",
    ["no error occurred", "The exception was handled", "the build did not fail"],
)
def test_descriptive_or_negated_error_text_remains_unknown(text) -> None:
    """否定或描述性错误词不应成为观察到的失败证据。"""
    chunks = extract((SourceMessage("tool", text),))
    assert chunks[0].outcome == "unknown"


@pytest.mark.parametrize("text", ["Traceback (most recent call last):", "FAILED test_parser"])
def test_explicit_failure_signals_are_retained(text) -> None:
    """明确 traceback 与测试失败继续被标记。"""
    chunks = extract((SourceMessage("tool", text),))
    assert chunks[0].outcome == "observed_failure"
