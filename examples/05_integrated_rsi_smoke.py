"""用独立开发 scope 验证真实模型调用、反馈和回放门禁，不输出密钥。"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

BROWSER_URL = os.environ.get("DMSA_SMOKE_BROWSER_URL", "http://127.0.0.1:8765")
MEMORY_URL = os.environ.get("DMSA_SMOKE_MEMORY_URL", "http://127.0.0.1:8000")


def _active_version(state: dict[str, Any], scope: str) -> str:
    """返回指定 scope 当前生效的策略版本."""
    return next(item["version"] for item in state["active"] if item["scope"] == scope)


def _baseline_version(state: dict[str, Any], scope: str) -> str:
    """返回该 scope 最早的基线版本.

    版本列表按写入时间倒序返回, 所以最早写入的基线在末尾。
    """
    return next(
        item["version"]
        for item in reversed(state["versions"])
        if item["scope"] == scope and json.loads(item["policy"])["neighbor_window"] == 1
    )


def main() -> int:
    """读取配置，用已知缺失上下文验证激活和回滚，不代表真实任务评分."""
    env = {}
    for line in (
        (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8-sig").splitlines()
    ):
        name, sep, value = line.partition("=")
        if sep and not name.lstrip().startswith("#"):
            env[name.strip()] = value.strip().strip('"').strip("'")
    scope = "rsi-smoke-development-" + uuid.uuid4().hex[:8]
    with httpx.Client(
        trust_env=False,
        base_url=MEMORY_URL,
        timeout=180,
        headers={"Authorization": "Bearer " + env["DMSA_MEMORY_API_KEY"]},
    ) as client:
        result = client.post(
            "/add",
            json={
                "user_id": scope,
                "session_id": "smoke",
                "request_id": "smoke",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "anchor " + "a " * 900 + "b " * 900 + "target evidence",
                    }
                ],
            },
        )
        result.raise_for_status()
        target_response = client.post(
            "/search", json={"query": "target evidence", "user_id": scope}
        )
        target_response.raise_for_status()
        relevant = target_response.json()["data"][0]["id"]
        output: dict[str, Any] = {"scope": scope}
        for _ in range(2):
            trace_response = client.post("/search", json={"query": "anchor", "user_id": scope})
            trace_response.raise_for_status()
            response = client.post(
                "/feedback",
                json={
                    "user_id": scope,
                    "trace_id": trace_response.json()["trace_id"],
                    "outcome": "failure",
                    "kind": "missing_context",
                    "relevant_ids": [relevant],
                },
            )
            output = {
                "scope": scope,
                "feedback_http_status": response.status_code,
                "result": response.json(),
            }
        evolution = response.json()["evolution"]
        output["evolution"] = evolution
        if response.is_success and evolution["status"] == "activated":
            with httpx.Client(trust_env=False) as browser:
                state = browser.get(BROWSER_URL + "/api/rsi").json()
            output["activated_version"] = evolution["version"]
            output["activated_version_is_active"] = (
                _active_version(state, scope) == evolution["version"]
            )
            baseline = _baseline_version(state, scope)
            output["baseline_version"] = baseline
            rollback = client.post("/rsi/rollback", json={"user_id": scope, "version": baseline})
            rollback.raise_for_status()
            with httpx.Client(trust_env=False) as browser:
                restored = browser.get(BROWSER_URL + "/api/rsi").json()
            output["rollback_restores_baseline_version"] = (
                _active_version(restored, scope) == baseline
            )
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return (
        0
        if output.get("activated_version_is_active")
        and output.get("rollback_restores_baseline_version")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
