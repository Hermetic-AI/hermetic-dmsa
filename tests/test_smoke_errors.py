"""烟雾探针的协议错误与命令行脱敏回归。"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from http.client import BadStatusLine, IncompleteRead, InvalidURL
from threading import Thread
from typing import Any

import pytest

from dmsa.errors import TransportError
from dmsa.mcp_server.http import smoke


def assert_fixed_failure(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(result.stderr)["message"] == "deployment smoke failed"
    assert "Traceback" not in result.stderr
    assert "private-upstream" not in result.stderr


@pytest.mark.parametrize("port", ["private-upstream-port", "99999", "-1"])
def test_cli_malformed_port_is_sanitized(port: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dmsa.mcp_server.http.smoke",
            "--health-only",
            "--url",
            f"http://127.0.0.1:{port}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_fixed_failure(result)


def test_cli_malformed_status_line_is_sanitized() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        port = listener.getsockname()[1]

        def serve() -> None:
            with listener.accept()[0] as connection:
                connection.recv(8192)
                connection.sendall(b"private-upstream-status\r\n\r\n")

        server = Thread(target=serve, daemon=True)
        server.start()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dmsa.mcp_server.http.smoke",
                    "--health-only",
                    "--url",
                    f"http://127.0.0.1:{port}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            assert_fixed_failure(result)
        finally:
            server.join(timeout=10)


@pytest.mark.parametrize(
    "failure",
    [
        BadStatusLine("private-upstream"),
        InvalidURL("private-upstream"),
        IncompleteRead(b"private-upstream", 100),
    ],
)
def test_protocol_failures_are_typed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(smoke, "urlopen", broken)
    with pytest.raises(TransportError, match=r"^smoke request failed$"):
        smoke.run_smoke("http://localhost", "key", health_only=True)


def test_request_construction_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("private-upstream")

    monkeypatch.setattr(smoke, "Request", broken)
    with pytest.raises(TransportError, match=r"^smoke request failed$"):
        smoke.run_smoke("http://localhost", "key", health_only=True)
