"""任务结果实体、轨迹扩展与闭环接口测试."""

from __future__ import annotations

import pytest

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.task_results import TaskResult, TaskResultError, TaskResultStore
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def _memory(tmp_path) -> CodingMemory:
    memory = CodingMemory(tmp_path / "task.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add("r", "u", "s", [SourceMessage("assistant", "def handler(): return 1")])
    return memory


def test_task_result_rejects_invalid_fields():
    with pytest.raises(TaskResultError):
        TaskResult(task_id="", scope="u", trace_id="t", outcome="success")
    with pytest.raises(TaskResultError):
        TaskResult(task_id="t", scope="u", trace_id="t", outcome="maybe")
    with pytest.raises(TaskResultError):
        TaskResult(task_id="t", scope="u", trace_id="t", outcome="success", tokens=-1)


def test_store_is_idempotent_and_scope_isolated(tmp_path):
    path = tmp_path / "tasks.sqlite"
    store = TaskResultStore(path)
    result = TaskResult(task_id="t1", scope="u", trace_id="tr", outcome="success", tokens=10)
    assert store.record(result) is True
    assert store.record(result) is False
    other = TaskResult(task_id="t1", scope="v", trace_id="tr", outcome="failure")
    assert store.record(other) is True
    conflicting = TaskResult(task_id="t1", scope="u", trace_id="tr", outcome="failure")
    with pytest.raises(TaskResultError):
        store.record(conflicting)
    assert store.summary("u") == {"total": 1, "success": 1, "failure": 0}
    assert store.summary("v") == {"total": 1, "success": 0, "failure": 1}
    store.close()


def test_trace_records_task_and_corpus(tmp_path):
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        trace = rsi.search("handler", "u", 5, task_id="task-7")
        store = RetrievalFeedbackStore(memory.store.path)
        detail = store.trace_detail("u", trace["trace_id"])
        store.close()
        assert detail["task_id"] == "task-7"
        assert detail["corpus_hash"]
        assert detail["hits"]
    finally:
        memory.close()


def test_task_result_roundtrip_through_integrated(tmp_path):
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        trace = rsi.search("handler", "u", 5, task_id="task-8")
        saved = rsi.record_task_result(
            "u",
            "task-8",
            trace["trace_id"],
            "success",
            tests_passed=3,
            files_changed=("src/app.py",),
            tokens=120,
            latency_ms=900,
            detail="pytest 通过",
        )
        assert saved["stored"] is True
        payload = rsi.task_results("u")
        assert payload["summary"] == {"total": 1, "success": 1, "failure": 0}
        assert payload["results"][0]["files_changed"] == ["src/app.py"]
    finally:
        memory.close()


def test_legacy_trace_table_gets_new_columns(tmp_path):
    path = tmp_path / "legacy.sqlite"
    store = RetrievalFeedbackStore(path)
    store.close()
    store = RetrievalFeedbackStore(path)
    columns = {row["name"] for row in store.db.execute("PRAGMA table_info(rsi_traces)")}
    store.close()
    assert {"task_id", "corpus_hash"} <= columns


def test_http_task_result_endpoint(tmp_path):
    """HTTP 契约可上报任务结果, 并拒绝非法终态."""
    from fastapi.testclient import TestClient

    from dmsa.mcp_server.http.app import create_app

    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        client = TestClient(create_app(memory, "test", rsi))
        headers = {"Authorization": "Bearer test"}
        trace = client.post(
            "/search", headers=headers, json={"query": "handler", "user_id": "u", "task_id": "t-9"}
        ).json()
        response = client.post(
            "/task_result",
            headers=headers,
            json={
                "user_id": "u",
                "task_id": "t-9",
                "trace_id": trace["trace_id"],
                "outcome": "success",
                "tests_passed": 1,
            },
        )
        assert response.status_code == 200, response.text
        assert rsi.task_results("u")["summary"]["success"] == 1
        invalid = client.post(
            "/task_result",
            headers=headers,
            json={
                "user_id": "u",
                "task_id": "t-10",
                "trace_id": trace["trace_id"],
                "outcome": "maybe",
            },
        )
        assert invalid.status_code == 422
    finally:
        memory.close()
