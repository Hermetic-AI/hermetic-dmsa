"""部署配置、容器入口与真实 HTTP 持久化测试。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from dmsa.errors import ConfigError, TransportError
from dmsa.mcp_server.http import cli


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("DMSA_"):
            monkeypatch.delenv(name)


def test_competition_requires_explicit_model(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("server started"))
    monkeypatch.setenv("DMSA_MEMORY_API_KEY", "private-service-key")
    monkeypatch.setenv("DMSA_EMBEDDING_API_KEY", "private-provider-key")
    monkeypatch.setenv("DMSA_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(SystemExit, match="DMSA_EMBEDDING_MODEL"):
        cli.main(["--profile", "competition", "--storage", str(path)])
    assert not path.exists()


def test_sparse_reads_mounted_secret(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dmsa.mcp_server.http.runtime import load_settings

    secret = tmp_path / "key"
    secret.write_text("mounted-secret\n", encoding="utf-8")
    monkeypatch.setenv("DMSA_MEMORY_API_KEY_FILE", str(secret))
    settings = load_settings("sparse-dev")
    assert settings.api_key == "mounted-secret"
    assert settings.endpoint is None
    assert "mounted-secret" not in repr(settings)


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_secret_rejects_empty_values(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from dmsa.mcp_server.http.runtime import load_settings

    monkeypatch.setenv("DMSA_MEMORY_API_KEY", value)
    with pytest.raises(ConfigError, match="DMSA_MEMORY_API_KEY"):
        load_settings("sparse-dev")


@pytest.mark.parametrize("case", ["missing", "blank", "ambiguous"])
def test_secret_file_fails_closed(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
) -> None:
    from dmsa.mcp_server.http.runtime import load_settings

    path = tmp_path / "secret"
    if case != "missing":
        path.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("DMSA_MEMORY_API_KEY_FILE", str(path))
    if case == "ambiguous":
        monkeypatch.setenv("DMSA_MEMORY_API_KEY", "private-value")
    with pytest.raises(ConfigError) as failure:
        load_settings("sparse-dev")
    assert "private-value" not in str(failure.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DMSA_EMBEDDING_MODEL", ""),
        ("DMSA_EMBEDDING_MODEL", "other-model"),
        ("DMSA_EMBEDDING_API_KEY", ""),
        ("DMSA_EMBEDDING_BASE_URL", ""),
        ("DMSA_EMBEDDING_BASE_URL", "file:///private"),
        ("DMSA_EMBEDDING_BASE_URL", "https://key:secret@example.invalid/v1"),
    ],
)
def test_competition_rejects_invalid_configuration(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    from dmsa.mcp_server.http.runtime import load_settings

    monkeypatch.setenv("DMSA_MEMORY_API_KEY", "service-secret")
    monkeypatch.setenv("DMSA_EMBEDDING_API_KEY", "provider-secret")
    monkeypatch.setenv("DMSA_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DMSA_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        load_settings("competition")


def test_competition_valid_configuration_and_dimension_boundary(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dmsa.mcp_server.http.runtime import load_settings

    monkeypatch.setenv("DMSA_MEMORY_API_KEY", "service-secret")
    monkeypatch.setenv("DMSA_EMBEDDING_API_KEY", "provider-secret")
    monkeypatch.setenv("DMSA_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DMSA_EMBEDDING_MODEL", "text-embedding-v4")
    assert load_settings("competition", dimension=1).dimension == 1
    with pytest.raises(ConfigError, match="dimension"):
        load_settings("competition", dimension=0)


def test_competition_accepts_qwen_native_embedding_configuration(
    clean_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接受 DashScope 原生 endpoint 与显式 Qwen 模型配置。"""
    from dmsa.mcp_server.http.runtime import load_settings

    monkeypatch.setenv("DMSA_MEMORY_API_KEY", "service-secret")
    monkeypatch.setenv("DMSA_EMBEDDING_API_KEY", "provider-secret")
    monkeypatch.setenv(
        "DMSA_EMBEDDING_BASE_URL",
        "https://workspace.example/api/v1/services/embeddings/text-embedding/text-embedding",
    )
    monkeypatch.setenv("DMSA_EMBEDDING_MODEL", "qwen3.7-text-embedding-flash")
    settings = load_settings("competition", dimension=1024)
    assert settings.embedding_model == "qwen3.7-text-embedding-flash"
    assert settings.dimension == 1024
    assert "provider-secret" not in repr(settings)


def test_smoke_contract_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    from dmsa.mcp_server.http import smoke

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise URLError("private-server-response")

    monkeypatch.setattr(smoke, "urlopen", broken)
    with pytest.raises(TransportError, match="smoke request failed") as failure:
        smoke.run_smoke("http://127.0.0.1:1", "secret")
    assert "private-server-response" not in str(failure.value)


def test_smoke_rejects_evidence_from_a_different_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from dmsa.mcp_server.http import smoke

    def unrelated(base: str, path: str, key: str, body: Any) -> Any:
        if path == "/health":
            return {"ok": True}
        return {"data": [{"id": "other", "content": smoke._CONTENT, "score": 1.0}]}

    monkeypatch.setattr(smoke, "_request", unrelated)
    with pytest.raises(TransportError, match="evidence"):
        smoke.run_smoke("http://localhost", "key", request_id="missing", search_only=True)


def test_smoke_health_only_needs_no_key_and_does_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    from dmsa.mcp_server.http import smoke

    def health(base: str, path: str, key: str, body: Any) -> Any:
        assert path == "/health"
        assert key == ""
        assert body is None
        return {"ok": True}

    monkeypatch.setattr(smoke, "_request", health)
    smoke.run_smoke("http://localhost", "", health_only=True)


@pytest.mark.parametrize("url", ["http://[", "file:///etc/passwd", "https://key@localhost"])
def test_smoke_rejects_invalid_urls(url: str) -> None:
    from dmsa.mcp_server.http.smoke import run_smoke

    with pytest.raises(TransportError):
        run_smoke(url, "key")


def test_real_http_restart_and_smoke_contract(tmp_path: Path) -> None:
    from dmsa.mcp_server.http.smoke import run_smoke

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "DMSA_MEMORY_API_KEY": "test-key"}
    env.pop("DMSA_MEMORY_API_KEY_FILE", None)
    command = [
        sys.executable,
        "-c",
        "from dmsa.mcp_server.http.cli import main; main()",
        "--port",
        str(port),
        "--storage",
        str(tmp_path / "restart.sqlite"),
    ]
    for search_only in (False, True):
        process = subprocess.Popen(
            command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail("HTTP server exited during startup")
                try:
                    with urlopen(base + "/health", timeout=0.2) as response:
                        assert response.status == 200
                    break
                except URLError:
                    time.sleep(0.1)
            else:
                pytest.fail("HTTP server did not become healthy")
            run_smoke(base, "test-key", request_id="restart-contract", search_only=search_only)
            with pytest.raises(TransportError):
                run_smoke(base, "wrong-key", request_id="restart-contract", search_only=True)
        finally:
            process.terminate()
            process.wait(timeout=10)
