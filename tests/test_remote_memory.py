import json

import httpx
import pytest

from dmsa.mcp_server.protocol import parse_request
from dmsa.mcp_server.remote_memory import RemoteMemoryMCPServer, configured_remote


@pytest.mark.parametrize(
    ("tool", "args", "path"),
    [
        ("dmsa_coding_memory_search", {"query": "anchor"}, "/search"),
        (
            "dmsa_coding_memory_record",
            {"request_id": "r", "session_id": "s", "content": "fact"},
            "/add",
        ),
        ("dmsa_coding_memory_feedback", {"trace_id": "t", "outcome": "success"}, "/feedback"),
        ("dmsa_coding_memory_status", {}, "/health"),
    ],
)
def test_stdio_tools_route_to_unified_http(tool, args, path):
    server = RemoteMemoryMCPServer("http://memory:8000", "test", "project")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer test"
        if request.method == "POST":
            assert json.loads(request.content)["user_id"] == "project"
        return httpx.Response(200, json={"ok": True})

    server.client.close()
    server.client = httpx.Client(
        base_url="http://memory:8000",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test"},
    )
    request = parse_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        )
    )
    response = server._call(request)
    assert "isError" not in response["result"]
    assert seen == [path]
    server.close()


def test_configured_remote_reads_only_local_key(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DMSA_MEMORY_API_KEY='local-test'\nOTHER=ignored\n", encoding="utf-8")
    monkeypatch.setenv("DMSA_ENV_FILE", str(path))
    monkeypatch.setenv("DMSA_CODING_MEMORY_URL", "http://memory:8000")
    monkeypatch.delenv("DMSA_MEMORY_API_KEY", raising=False)
    server = configured_remote("project")
    assert server is not None
    assert server.client.headers["Authorization"] == "Bearer local-test"
    server.close()
