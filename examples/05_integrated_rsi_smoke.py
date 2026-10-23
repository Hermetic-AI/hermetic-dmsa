"""用独立开发 scope 验证真实模型调用、反馈和回放门禁，不输出密钥。"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import httpx


def main() -> int:
    """读取配置，用已知缺失上下文验证激活和回滚，不代表真实任务评分。"""
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
        base_url="http://127.0.0.1:8000",
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
        output = {}
        for _ in range(2):
            response = client.post("/search", json={"query": "anchor", "user_id": scope})
            response.raise_for_status()
            trace = response.json()
            response = client.post(
                "/feedback",
                json={
                    "user_id": scope,
                    "trace_id": trace["trace_id"],
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
        if response.is_success and response.json()["evolution"]["status"] == "activated":
            hits = client.post("/search", json={"query": "anchor", "user_id": scope}).json()["data"]
            output["activated_retrieves_target"] = relevant in [h["id"] for h in hits]
            with httpx.Client(trust_env=False) as browser:
                versions = browser.get("http://127.0.0.1:8765/api/rsi").json()["versions"]
            baseline = next(
                v["version"]
                for v in versions
                if v["scope"] == scope and json.loads(v["policy"])["neighbor_window"] == 1
            )
            rollback = client.post("/rsi/rollback", json={"user_id": scope, "version": baseline})
            rollback.raise_for_status()
            restored = client.post("/search", json={"query": "anchor", "user_id": scope}).json()[
                "data"
            ]
            output["rollback_restores_baseline"] = relevant not in [h["id"] for h in restored]
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        return (
            0
            if output.get("activated_retrieves_target") and output.get("rollback_restores_baseline")
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
