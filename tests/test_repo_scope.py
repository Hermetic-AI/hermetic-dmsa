"""仓库维度 scope 的隔离、契约与 RSI 轨迹测试."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.policy import OfflineRSIError
from dmsa.mcp_server.http.app import create_app
from dmsa.memory import (
    CodingMemory,
    CodingMemoryValidationError,
    SearchPolicy,
    SourceMessage,
    scope_key,
)

ALPHA = (
    "diff --git a/src/alpha.py b/src/alpha.py\n"
    "+++ b/src/alpha.py\n+def alpha_token():\n+    return 1\n"
)
BETA = (
    "diff --git a/src/beta.py b/src/beta.py\n+++ b/src/beta.py\n+def beta_token():\n+    return 2\n"
)
POLICY = SearchPolicy(dense_weight=0.0)


def test_scope_key_only_changes_with_repo():
    assert scope_key("proj") == "proj"
    assert scope_key("proj", "") == "proj"
    assert scope_key("proj", "alpha") == "proj#alpha"


def test_repo_partitions_isolate_evidence(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-a", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="alpha")
        memory.add("req-b", "proj", "sess", [SourceMessage("assistant", BETA)], repo="beta")
        alpha = memory.search("alpha_token", "proj", 5, POLICY, repo="alpha")
        beta = memory.search("beta_token", "proj", 5, POLICY, repo="beta")
        unscoped = memory.search("alpha_token", "proj", 5, POLICY)
        assert alpha
        assert "alpha.py" in alpha[0].content
        assert beta
        assert "beta.py" in beta[0].content
        assert all("alpha.py" not in hit.content for hit in unscoped)
        crossed = memory.search("beta_token", "proj", 5, POLICY, repo="alpha")
        assert all("beta.py" not in hit.content for hit in crossed)
    finally:
        memory.close()


def test_repo_dimension_is_additive_for_legacy_writes(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-legacy", "proj", "sess", [SourceMessage("assistant", ALPHA)])
        assert memory.search("alpha_token", "proj", 5, POLICY)
        assert memory.search("alpha_token", "proj", 5, POLICY, repo="alpha") == []
        memory.delete_scope(scope_key("proj", ""))
        assert memory.search("alpha_token", "proj", 5, POLICY) == []
    finally:
        memory.close()


def test_repo_rejects_invalid_identifiers(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.add("req-x", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="a\0b")
        with pytest.raises(CodingMemoryValidationError):
            memory.search("alpha_token", "proj", 5, POLICY, repo=123)
    finally:
        memory.close()


def test_delete_scope_removes_every_repo_partition(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-a", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="alpha")
        memory.add("req-b", "proj", "sess", [SourceMessage("assistant", BETA)], repo="beta")
        memory.add("req-c", "proj", "sess", [SourceMessage("assistant", ALPHA)])
        memory.delete_scope("proj")
        assert memory.search("alpha_token", "proj", 5, POLICY, repo="alpha") == []
        assert memory.search("beta_token", "proj", 5, POLICY, repo="beta") == []
        assert memory.search("alpha_token", "proj", 5, POLICY) == []
        # scope_epochs 保留 tombstone 以维持指纹单调, 但三个分区的数据都必须清空。
        assert memory.store.rows(scope_key("proj", "alpha")) == []
        assert memory.store.rows(scope_key("proj", "beta")) == []
        assert memory.store.rows("proj") == []
    finally:
        memory.close()


def test_delete_scope_with_repo_keeps_sibling_partitions(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-a", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="alpha")
        memory.add("req-b", "proj", "sess", [SourceMessage("assistant", BETA)], repo="beta")
        memory.delete_scope("proj", repo="alpha")
        assert memory.search("alpha_token", "proj", 5, POLICY, repo="alpha") == []
        assert memory.search("beta_token", "proj", 5, POLICY, repo="beta")
        assert memory.store.rows(scope_key("proj", "alpha")) == []
        assert memory.store.rows(scope_key("proj", "beta"))
    finally:
        memory.close()


def test_scope_separator_is_rejected_in_identifiers(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        with pytest.raises(CodingMemoryValidationError):
            scope_key("proj#other")
        with pytest.raises(CodingMemoryValidationError):
            scope_key("proj", "alpha#beta")
        with pytest.raises(CodingMemoryValidationError):
            memory.add("req-x", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="a#b")
    finally:
        memory.close()


def test_snapshot_is_scoped_to_the_repo_partition(tmp_path):
    repo_root = tmp_path / "workspace"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "app.py").write_text("class Handler:\n    pass\n", encoding="utf-8")
    evidence = "diff --git a/src/gone.py b/src/gone.py\n+def legacy_method():\n    pass\n"
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-a", "proj", "sess", [SourceMessage("assistant", evidence)], repo="alpha")
        memory.add("req-b", "proj", "sess", [SourceMessage("assistant", evidence)], repo="beta")
        memory.save_repo_snapshot("proj", repo_root, repo="alpha")
        alpha = memory.search("legacy_method", "proj", 5, POLICY, repo="alpha")
        beta = memory.search("legacy_method", "proj", 5, POLICY, repo="beta")
        assert alpha
        assert "[stale]" in alpha[0].content
        assert beta
        assert "[stale]" not in beta[0].content
    finally:
        memory.close()


def test_http_contract_carries_repo(tmp_path):
    memory = CodingMemory(tmp_path / "http.sqlite", policy=POLICY)
    client = TestClient(create_app(memory, "secret"))
    headers = {"Authorization": "Bearer secret"}
    try:
        added = client.post(
            "/add",
            headers=headers,
            json={
                "request_id": "req-a",
                "user_id": "proj",
                "session_id": "sess",
                "repo": "alpha",
                "messages": [{"role": "assistant", "content": ALPHA}],
            },
        )
        assert added.status_code == 200
        assert added.json()["repo"] == "alpha"
        hit = client.post(
            "/search",
            headers=headers,
            json={"query": "alpha_token", "user_id": "proj", "repo": "alpha"},
        ).json()
        assert hit["data"]
        empty = client.post(
            "/search", headers=headers, json={"query": "alpha_token", "user_id": "proj"}
        ).json()
        assert empty["data"] == []
        rejected = client.post(
            "/search",
            headers=headers,
            json={"query": "alpha_token", "user_id": "proj", "repo": 7},
        )
        assert rejected.status_code == 422
    finally:
        memory.close()


def test_rsi_trace_records_repo_and_validates_labels_in_partition(tmp_path):
    path = tmp_path / "rsi.sqlite"
    memory = CodingMemory(path, policy=POLICY)
    rsi = IntegratedRSI(memory)
    store = RetrievalFeedbackStore(path)
    try:
        memory.add("req-a", "proj", "sess", [SourceMessage("assistant", ALPHA)], repo="alpha")
        memory.add("req-b", "proj", "sess", [SourceMessage("assistant", BETA)], repo="beta")
        payload = rsi.search("alpha_token", "proj", 5, task_id="task-a", dense=False, repo="alpha")
        assert payload["repo"] == "alpha"
        detail = store.trace_detail("proj", payload["trace_id"])
        assert detail["repo"] == "alpha"
        relevant = [item["id"] for item in payload["data"]]
        assert relevant
        assert store.feedback("proj", payload["trace_id"], "success", "unknown", relevant)
        cases = store.cases("proj")
        assert cases
        assert cases[0].user_id == "proj#alpha"
        with pytest.raises(OfflineRSIError):
            store.feedback("proj", payload["trace_id"], "failure", "exact_miss", ["beta-id"])
    finally:
        store.close()
        memory.close()
