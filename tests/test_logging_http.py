"""HTTP 请求日志测试：trace 绑定、级别选择与脱敏。"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dmsa.mcp_server.http.app import create_app
from dmsa.memory import CodingMemory, SearchPolicy


@pytest.fixture
def client(tmp_path) -> Any:
    """返回挂了请求日志中间件的测试客户端。"""
    memory = CodingMemory(tmp_path / "log.sqlite", policy=SearchPolicy(dense_weight=0.0))
    app_client = TestClient(create_app(memory, "secret"))
    yield app_client
    memory.close()


def _records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    """挑出指定消息名的日志记录。"""
    return [record for record in caplog.records if record.getMessage() == message]


def test_completed_line_carries_trace_and_duration(client, caplog) -> None:
    """4xx 请求记录状态、客户端与耗时，且 trace 与响应头一致。"""
    caplog.set_level(logging.INFO)
    response = client.post("/search", json={"query": "bug", "user_id": "u"})
    assert response.status_code == 401
    completed = _records(caplog, "request completed")
    assert len(completed) == 1
    record = completed[0]
    assert record.status == 401
    assert record.method == "POST"
    assert record.path == "/search"
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    assert response.headers["x-request-id"]
    assert _records(caplog, "unauthorized request")


def test_incoming_request_id_is_reused(client, caplog) -> None:
    """上游给了 X-Request-ID 就沿用，方便跨服务串日志。"""
    caplog.set_level(logging.INFO)
    response = client.get("/health", headers={"X-Request-ID": "upstream-1"})
    assert response.headers["x-request-id"] == "upstream-1"
    assert response.status_code == 200


def test_health_check_is_quiet_at_info(client, caplog) -> None:
    """健康检查在 INFO 下不打请求行，容器健康探针不会淹没日志。"""
    caplog.set_level(logging.INFO)
    client.get("/health")
    assert _records(caplog, "request completed") == []


def test_success_logged_at_debug_only(client, caplog) -> None:
    """成功的业务请求只在 DEBUG 打完成行；健康检查任何级别都走 DEBUG。"""
    headers = {"Authorization": "Bearer secret"}
    payload = {
        "request_id": "req-log-1",
        "user_id": "u",
        "session_id": "s",
        "messages": [{"role": "user", "content": "fix the flaky test"}],
    }
    caplog.set_level(logging.INFO)
    assert client.post("/add", json=payload, headers=headers).status_code == 200
    assert _records(caplog, "request completed") == []
    caplog.set_level(logging.DEBUG)
    assert client.post("/search", json={"query": "x", "user_id": "u"}, headers=headers)
    completed = _records(caplog, "request completed")
    assert completed
    assert completed[-1].status == 200


def test_invalid_body_logs_sanitized_warning(client, caplog) -> None:
    """请求体不合法时只记录字段数量，不回显用户内容。"""
    caplog.set_level(logging.WARNING)
    response = client.post("/add", json={"nope": 1}, headers={"Authorization": "Bearer secret"})
    assert response.status_code == 422
    rejected = _records(caplog, "add rejected by schema")
    assert len(rejected) == 1
    assert "nope" not in caplog.text
    assert response.json()["detail"] == "invalid request"
