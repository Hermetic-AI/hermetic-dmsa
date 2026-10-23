from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.model_advisor import PolicyAdvice
from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy
from dmsa.gradient.replay_models import digest
from dmsa.inspect.rsi_data import read_rsi
from dmsa.mcp_server.http.app import create_app
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def build(tmp_path):
    memory = CodingMemory(tmp_path / "integrated.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add(
        "r", "u", "s", [SourceMessage("assistant", "anchor " + "a " * 1000 + "target evidence")]
    )
    rsi = IntegratedRSI(memory)
    # The last slice contains context absent from the query-matching first slice.
    relevant = memory.search("target evidence", "u", 5)[0].id
    return memory, rsi, relevant


def test_feedback_replay_activation_restart_and_rollback(tmp_path):
    memory, rsi, relevant = build(tmp_path)
    client = TestClient(create_app(memory, "test", rsi))
    headers = {"Authorization": "Bearer test"}
    for _ in range(2):
        trace = client.post(
            "/search", headers=headers, json={"query": "anchor", "user_id": "u"}
        ).json()
        response = client.post(
            "/feedback",
            headers=headers,
            json={
                "user_id": "u",
                "trace_id": trace["trace_id"],
                "outcome": "failure",
                "kind": "missing_context",
                "relevant_ids": [relevant],
            },
        )
        assert response.status_code == 200, response.text
    assert response.json()["evolution"]["status"] == "activated"
    assert relevant in [h["id"] for h in IntegratedRSI(memory).search("anchor", "u", 5)["data"]]
    baseline = digest(asdict(memory.policy))
    rsi.rollback("u", baseline)
    assert relevant not in [h["id"] for h in rsi.search("anchor", "u", 5)["data"]]
    assert {e["kind"] for e in read_rsi(tmp_path / "integrated.sqlite")["events"]} >= {
        "activated",
        "rollback",
        "replay_passed",
    }
    memory.close()


def test_model_pipeline_records_calls_and_preserves_feedback_on_error(tmp_path, monkeypatch):
    memory, rsi, relevant = build(tmp_path)
    rsi.models = True
    store = RetrievalFeedbackStore(memory.store.path)
    for _ in range(2):
        store.feedback(
            "u", rsi.search("anchor", "u", 5)["trace_id"], "failure", "missing_context", [relevant]
        )

    class FakeProposer:
        decision_model = "test-decision"
        text_model = "test-text"
        observer = None

        def propose(self, baseline, diagnostics, **kwargs):
            self.observer({"model": self.decision_model, "status": "succeeded"})
            return (PolicyAdvice(0, baseline.changed(neighbor_window=1), "test explanation"),)

        def close(self):
            pass

    monkeypatch.setattr(
        "dmsa.gradient.integrated.ModelAssistedPolicyProposer.from_environment",
        lambda: FakeProposer(),
    )
    assert rsi.evolve("u")["status"] == "activated"
    assert "model_call" in {e["kind"] for e in read_rsi(tmp_path / "integrated.sqlite")["events"]}
    store.close()
    memory.close()


def test_corpus_mutation_and_stale_baseline_cannot_activate(tmp_path):
    memory, rsi, _ = build(tmp_path)
    store = RetrievalFeedbackStore(memory.store.path)
    baseline = RetrievalPolicy(memory.policy)
    fingerprint = memory.corpus_state(("u",)).fingerprint
    memory.add("new", "u", "new", [SourceMessage("assistant", "new corpus")])
    with pytest.raises(OfflineRSIError):
        rsi._activate(store, "u", baseline, baseline.changed(neighbor_window=1), fingerprint)
    assert read_rsi(tmp_path / "integrated.sqlite")["active"] == []
    store.close()
    memory.close()


def test_model_outage_keeps_the_replay_gate_and_feedback(tmp_path, monkeypatch):
    memory, rsi, relevant = build(tmp_path)
    rsi.models = True
    store = RetrievalFeedbackStore(memory.store.path)
    for _ in range(2):
        store.feedback(
            "u", rsi.search("anchor", "u", 5)["trace_id"], "failure", "missing_context", [relevant]
        )

    def unavailable():
        raise OfflineRSIError("provider unavailable")

    monkeypatch.setattr(
        "dmsa.gradient.integrated.ModelAssistedPolicyProposer.from_environment", unavailable
    )
    assert rsi.evolve("u")["status"] == "activated"
    events = read_rsi(tmp_path / "integrated.sqlite")["events"]
    assert {e["kind"] for e in events} >= {"model_fallback", "replay_passed", "activated"}
    assert len(store.cases("u")) == 2
    store.close()
    memory.close()


def test_unknown_feedback_no_promotion_and_scope_isolation(tmp_path):
    memory, rsi, relevant = build(tmp_path)
    store = RetrievalFeedbackStore(memory.store.path)
    trace = rsi.search("anchor", "u", 5)["trace_id"]
    with pytest.raises(OfflineRSIError):
        store.feedback("other", trace, "failure", "missing_context", [relevant])
    store.feedback("u", trace, "failure", "unknown", [relevant])
    assert not store.feedback("u", trace, "failure", "unknown", [relevant])
    with pytest.raises(OfflineRSIError):
        store.feedback("u", trace, "success", "unknown", [relevant])
    assert rsi.evolve("u")["status"] == "insufficient_feedback"
    with pytest.raises(OfflineRSIError):
        rsi.rollback("other", "0" * 64)
    store.close()
    memory.close()


def test_gate_rejects_no_improvement_and_http_auth(tmp_path):
    memory, rsi, relevant = build(tmp_path)
    client = TestClient(create_app(memory, "test", rsi))
    assert client.post("/rsi/evolve", json={"user_id": "u"}).status_code == 401
    store = RetrievalFeedbackStore(memory.store.path)
    for _ in range(2):
        trace = rsi.search("target evidence", "u", 5)["trace_id"]
        store.feedback("u", trace, "failure", "exact_miss", [relevant])
    assert rsi.evolve("u")["status"] == "rejected"
    assert read_rsi(tmp_path / "integrated.sqlite")["active"] == []
    store.close()
    memory.close()
