"""显式执行的 Docker 验收, 独立项目和临时命名卷隔离测试数据。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Iterator
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def stack() -> Iterator[tuple[list[str], dict[str, str]]]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {name: value for name, value in os.environ.items() if not name.startswith("DMSA_")}
    env["DMSA_HTTP_PORT"] = str(port)
    command = ["docker", "compose", "-p", "dmsa-contract-" + uuid4().hex[:10]]
    try:
        subprocess.run(
            [*command, "up", "-d", "--build", "--wait", "--wait-timeout", "120"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        yield command, env
    finally:
        subprocess.run(
            [*command, "down", "--volumes"], env=env, check=True, capture_output=True, text=True
        )


def test_container_health_auth_and_image_metadata(stack) -> None:
    command, env = stack
    subprocess.run([*command, "exec", "-T", "memory", "dmsa-memory-smoke"], env=env, check=True)
    result = subprocess.run(
        [*command, "ps", "-q", "memory"], env=env, check=True, capture_output=True, text=True
    )
    container = json.loads(subprocess.check_output(["docker", "inspect", result.stdout.strip()]))[0]
    assert container["Config"]["User"] == "10001:10001"
    assert container["Config"]["Labels"]["org.opencontainers.image.licenses"] == "Apache-2.0"
    assert container["State"]["Health"]["Status"] == "healthy"
    assert container["HostConfig"]["ReadonlyRootfs"] is True
    subprocess.run(
        [
            *command,
            "exec",
            "-T",
            "memory",
            "python",
            "-c",
            "from pathlib import Path; root = Path('/usr/share/licenses/hermetic-dmsa'); "
            "assert (root / 'LICENSE').is_file(); assert (root / 'NOTICE').is_file()",
        ],
        env=env,
        check=True,
    )
    failed = subprocess.run(
        [
            *command,
            "exec",
            "-T",
            "-e",
            "DMSA_MEMORY_API_KEY=wrong",
            "memory",
            "dmsa-memory-smoke",
            "--search-only",
        ],
        env=env,
    )
    assert failed.returncode != 0


def test_recreated_container_preserves_named_volume(stack) -> None:
    command, env = stack
    subprocess.run(
        [
            *command,
            "exec",
            "-T",
            "memory",
            "dmsa-memory-smoke",
            "--request-id",
            "container-recreation",
        ],
        env=env,
        check=True,
    )
    subprocess.run([*command, "up", "-d", "--force-recreate", "--wait"], env=env, check=True)
    subprocess.run(
        [
            *command,
            "exec",
            "-T",
            "memory",
            "dmsa-memory-smoke",
            "--search-only",
            "--request-id",
            "container-recreation",
        ],
        env=env,
        check=True,
    )


@pytest.mark.parametrize(
    "missing",
    [
        "DMSA_MEMORY_API_KEY",
        "DMSA_EMBEDDING_BASE_URL",
        "DMSA_EMBEDDING_API_KEY",
        "DMSA_EMBEDDING_MODEL",
    ],
)
def test_competition_container_fails_before_storage_without_credentials(stack, missing) -> None:
    _, env = stack
    configured = {
        "DMSA_MEMORY_API_KEY": "test-service-key",
        "DMSA_EMBEDDING_BASE_URL": "https://example.invalid/v1",
        "DMSA_EMBEDDING_API_KEY": "test-provider-key",
        "DMSA_EMBEDDING_MODEL": "text-embedding-v4",
    }
    environment = [
        part
        for name, value in configured.items()
        if name != missing
        for part in ("-e", f"{name}={value}")
    ]
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            *environment,
            "hermetic-dmsa:local",
            "--profile",
            "competition",
            "--storage",
            "/unwritable/memory.sqlite",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert missing in result.stderr
    assert "Traceback" not in result.stderr


def test_competition_compose_disables_development_defaults(stack) -> None:
    command, env = stack
    result = subprocess.run(
        [
            *command,
            "-f",
            "compose.yaml",
            "-f",
            "deploy/compose.competition.yaml",
            "config",
            "--format",
            "json",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    service = json.loads(result.stdout)["services"]["memory"]
    assert service["command"][1] == "competition"
    assert service["environment"]["DMSA_MEMORY_API_KEY"] == ""
    assert service["environment"]["DMSA_EMBEDDING_MODEL"] == ""
    assert service["volumes"] == [
        {"type": "volume", "source": "competition-data", "target": "/data", "volume": {}}
    ]
