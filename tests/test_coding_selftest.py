"""本地自出题台与官方 HTTP 契约的端到端联测."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.coding.selftest.cases import build_cases  # noqa: E402
from benchmarks.coding.selftest.solver import solve  # noqa: E402
from dmsa.mcp_server.http.app import create_app  # noqa: E402
from dmsa.memory import CodingMemory, SearchPolicy  # noqa: E402


def test_http_selftest_solves_from_retrieved_evidence(tmp_path) -> None:
    """走真实 /add /search 合同, 用检索证据打补丁并通过 pytest."""
    case = build_cases(0)[0]
    memory = CodingMemory(tmp_path / "selftest.sqlite", policy=SearchPolicy(dense_weight=0.0))
    client = TestClient(create_app(memory, "key"))
    headers = {"Authorization": "Bearer key"}
    for index, session in enumerate(case.sessions):
        response = client.post(
            "/add",
            headers=headers,
            json={
                "request_id": f"rel-{index}",
                "user_id": "selftest",
                "session_id": f"rel-{index}",
                "messages": [
                    {"role": role, "content": content} for role, content in session
                ],
            },
        )
        assert response.status_code == 200
    for index, text in enumerate(case.noise[:8]):
        response = client.post(
            "/add",
            headers=headers,
            json={
                "request_id": f"noise-{index}",
                "user_id": "selftest",
                "session_id": f"noise-{index}",
                "messages": [{"role": "assistant", "content": text}],
            },
        )
        assert response.status_code == 200
    reply = client.post(
        "/search",
        headers=headers,
        json={"query": case.query, "user_id": "selftest", "top_k": 100},
    )
    assert reply.status_code == 200
    hits = reply.json()["data"]
    assert len(hits) > 4
    assert any(case.gold_text in hit["content"] for hit in hits)
    repo = tmp_path / "repo"
    for path, content in case.files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    result = solve([hit["content"] for hit in hits], repo, case.test_file, 3)
    assert result.resolved
    assert result.localized_path == case.target_path
    memory.close()
