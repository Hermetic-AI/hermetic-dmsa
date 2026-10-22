# ruff: noqa: E501, RUF002
"""HTTP Coding Memory 协议测试。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dmsa.mcp_server.http import cli
from dmsa.mcp_server.http.app import create_app
from dmsa.memory import CodingMemory, SearchPolicy


def test_http_auth_add_search_and_idempotency(tmp_path) -> None:
    """健康检查公开，写入/检索鉴权且可按 user scope 查找。"""
    memory = CodingMemory(tmp_path / "http.sqlite", policy=SearchPolicy(dense_weight=0.0))
    client = TestClient(create_app(memory, "secret"))
    assert client.get("/health").status_code == 200
    assert client.post("/search", json={"query": "bug", "user_id": "u"}).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    payload = {"request_id": "opaque/id", "user_id": "u", "session_id": "s", "messages": [{"role": "user", "content": [{"type": "text", "text": "bug "}, {"type": "text", "text": "fixed"}]}]}
    reply = client.post("/add", json=payload, headers=headers)
    assert reply.status_code == 200
    assert reply.json() == {"success": True, "request_id": "opaque/id", "user_id": "u", "session_id": "s"}
    assert client.post("/add", json=payload, headers=headers).status_code == 200
    assert client.post("/search", json={"query": "bug", "user_id": "other", "top_k": 2}, headers=headers).json() == {"data": []}
    assert client.post("/search", json={"query": "bug", "user_id": "u", "top_k": True}, headers=headers).status_code == 422
    assert client.post("/search", json={"query": "bug", "user_id": "u", "options": ["dense"]}, headers=headers).status_code == 422
    memory.close()


def test_http_conflict_and_token_auth(tmp_path) -> None:
    """冲突 payload 返回 409，Token 认证可用。"""
    memory = CodingMemory(tmp_path / "conflict.sqlite", None)
    client = TestClient(create_app(memory, "k"))
    headers = {"Authorization": "Token k"}
    base = {"request_id": "r", "user_id": "u", "session_id": "s", "messages": [{"role": "assistant", "content": "one"}]}
    assert client.post("/add", json=base, headers=headers).status_code == 200
    base["messages"] = [{"role": "assistant", "content": "two"}]
    assert client.post("/add", json=base, headers=headers).status_code == 409
    memory.close()


def test_http_restart_concurrency_and_sanitized_body(tmp_path) -> None:
    """并发重试仍幂等，重启可检索，错误体不回显输入。"""
    path = tmp_path / "restart.sqlite"
    memory = CodingMemory(path, policy=SearchPolicy(dense_weight=0.0))
    client = TestClient(create_app(memory, "key-1"))
    headers = {"X-Api-Key": "key-1"}
    bad = client.post("/add", content='{"request_id":"secret source text"', headers=headers)
    assert bad.status_code == 422
    assert "secret source text" not in bad.text
    payload = {
        "request_id": "r1",
        "user_id": "u1",
        "session_id": "s1",
        "messages": [{"role": "user", "content": "durable deployment clue"}],
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: client.post("/add", json=payload, headers=headers).status_code, range(8)))
    assert replies == [200] * 8
    memory.close()
    reopened = CodingMemory(path, policy=SearchPolicy(dense_weight=0.0))
    response = TestClient(create_app(reopened, "key-1")).post("/search", json={"query": "deployment", "user_id": "u1", "top_k": 1}, headers=headers)
    assert response.json()["data"]
    reopened.close()


def test_cli_validates_key_before_server_start(monkeypatch) -> None:
    """缺服务密钥时不构造数据库或启动 server。"""
    monkeypatch.delenv("DMSA_MEMORY_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="DMSA_MEMORY_API_KEY"):
        cli.main(["--profile", "sparse-dev"])



