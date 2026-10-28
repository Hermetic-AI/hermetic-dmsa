"""对已部署的 Coding Memory 服务做公网接口自检, 只输出状态与耗时, 不输出密钥."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import httpx

ORIGIN = os.environ.get("DMSA_SELF_TEST_ORIGIN", "https://jensenlyz.cn").rstrip("/")
PREFIX = os.environ.get("DMSA_SELF_TEST_PREFIX", "/v1/memories")
API_KEY = os.environ.get("DMSA_MEMORY_API_KEY", "").strip()


def _call(
    client: httpx.Client,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行一次请求, 记录状态码、耗时与响应摘要."""
    started = time.perf_counter()
    response = client.request(method, path, json=payload)
    elapsed = time.perf_counter() - started
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:200]
    return {
        "method": method,
        "path": path,
        "status": response.status_code,
        "elapsed_s": round(elapsed, 3),
        "body": body,
    }


def _run(client: httpx.Client, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按顺序执行步骤, 供后续步骤引用的字段保存在上下文里."""
    context: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    for step in steps:
        payload = step.get("payload")
        if callable(payload):
            payload = payload(context)
        result = _call(client, step["method"], step["path"], payload)
        result["name"] = step["name"]
        context[step["name"]] = result
        results.append(result)
    return results


def main() -> int:
    """依次调用全部公网接口, 输出 JSON 报告与失败原因."""
    if not API_KEY:
        sys.stderr.write("DMSA_MEMORY_API_KEY 未设置, 无法执行带认证的自检.\n")
        return 2
    scope = "self-test-" + uuid.uuid4().hex[:10]
    request_id = "self-test-" + uuid.uuid4().hex[:8]
    trace_path = f"{PREFIX}/search"

    def search_payload(context: dict[str, Any]) -> dict[str, Any]:
        """用真实检索结果构造反馈与任务上报载荷."""
        return {"query": "clamp_0 负数 边界", "user_id": scope, "top_k": 5}

    def feedback_payload(context: dict[str, Any]) -> dict[str, Any]:
        """引用上一步检索返回的 trace_id."""
        body = context.get("search", {}).get("body", {})
        return {
            "user_id": scope,
            "trace_id": body.get("trace_id", "missing"),
            "outcome": "success",
            "kind": "unknown",
            "relevant_ids": [],
        }

    def task_payload(context: dict[str, Any]) -> dict[str, Any]:
        """任务上报只记录事实字段."""
        body = context.get("search", {}).get("body", {})
        return {
            "user_id": scope,
            "task_id": "self-test-task",
            "trace_id": body.get("trace_id", "missing"),
            "outcome": "success",
            "tests_passed": 1,
            "files_changed": [],
            "tokens": 0,
            "latency_ms": 0,
            "detail": "公网接口自检",
        }

    steps = [
        {
            "name": "health",
            "method": "GET",
            "path": "/health",
        },
        {
            "name": "add",
            "method": "POST",
            "path": f"{PREFIX}/add",
            "payload": {
                "request_id": request_id,
                "user_id": scope,
                "session_id": "self-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "问题：clamp_0 对负数返回错误。\n"
                            "根因：下界比较用了严格大于。\n"
                            "改动：改为 max(lower, min(upper, value))。\n"
                            "验证：pytest 全部通过。\n"
                            "```python path=ops_0_clamp.py\n"
                            "def clamp_0(value, lower, upper):\n"
                            "    return max(lower, min(upper, value))\n"
                            "```"
                        ),
                    }
                ],
            },
        },
        {
            "name": "search",
            "method": "POST",
            "path": trace_path,
            "payload": search_payload,
        },
        {
            "name": "feedback",
            "method": "POST",
            "path": f"{PREFIX}/feedback",
            "payload": feedback_payload,
        },
        {
            "name": "task_result",
            "method": "POST",
            "path": f"{PREFIX}/task_result",
            "payload": task_payload,
        },
        {
            "name": "rsi_propose",
            "method": "POST",
            "path": f"{PREFIX}/rsi/propose",
            "payload": {"user_id": scope},
        },
        {
            "name": "rsi_evolve",
            "method": "POST",
            "path": f"{PREFIX}/rsi/evolve",
            "payload": {"user_id": scope},
        },
    ]

    headers = {"Authorization": f"Bearer {API_KEY}"}
    with httpx.Client(trust_env=False, timeout=180, headers=headers, base_url=ORIGIN) as client:
        results = _run(client, steps)
        invalid = _call(
            client,
            "POST",
            f"{PREFIX}/add",
            {"request_id": "bad-body"},
        )
        with httpx.Client(trust_env=False, timeout=60, base_url=ORIGIN) as anonymous:
            unauthorized = _call(anonymous, "POST", f"{PREFIX}/add", {"request_id": "no-auth"})

    expectations = {
        "health": 200,
        "add": 200,
        "search": 200,
        "feedback": 200,
        "task_result": 200,
        "rsi_propose": 200,
        "rsi_evolve": 200,
    }
    failures: list[str] = []
    for result in results:
        expected = expectations.get(result["name"])
        if expected is not None and result["status"] != expected:
            failures.append(f"{result['name']} 期望 {expected} 实际 {result['status']}")
    if unauthorized["status"] != 401:
        failures.append(f"无认证期望 401 实际 {unauthorized['status']}")
    if invalid["status"] != 422:
        failures.append(f"非法请求体期望 422 实际 {invalid['status']}")
    search_body = next(r for r in results if r["name"] == "search")["body"]
    if not isinstance(search_body, dict) or not search_body.get("trace_id"):
        failures.append("search 未返回 trace_id")
    hits = search_body.get("data", []) if isinstance(search_body, dict) else []
    if not hits:
        failures.append("search 未返回任何证据")

    report = {
        "origin": ORIGIN,
        "prefix": PREFIX,
        "scope": scope,
        "steps": results,
        "negative": {
            "unauthorized": unauthorized,
            "invalid_body": invalid,
        },
        "search_hit_count": len(hits),
        "search_trace_id_present": bool(
            isinstance(search_body, dict) and search_body.get("trace_id")
        ),
        "failures": failures,
        "passed": not failures,
    }
    # 报告含中文证据, 直接写 UTF-8 字节, 避免重定向时被本地编码改写.
    sys.stdout.buffer.write(
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
