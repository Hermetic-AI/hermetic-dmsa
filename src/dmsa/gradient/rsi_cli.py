# ruff: noqa: D415, RUF002
"""为离线 RSI 提供有界的模型辅助候选建议 CLI。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Any

from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer
from dmsa.gradient.policy import FailureDiagnostic, OfflineRSIError, RetrievalPolicy

_MAX_INPUT_BYTES = 64 * 1024


def _parse_payload(raw: bytes) -> tuple[RetrievalPolicy, tuple[FailureDiagnostic, ...]]:
    """解析限长 JSON，且只允许策略字段及聚合诊断计数。"""
    if len(raw) > _MAX_INPUT_BYTES:
        raise OfflineRSIError("input exceeds the 64 KiB limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OfflineRSIError("input must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise OfflineRSIError("input must be a JSON object")
    current = payload.get("current_policy", {})
    diagnostics_data = payload.get("diagnostics")
    if not isinstance(current, dict) or not isinstance(diagnostics_data, list):
        raise OfflineRSIError("current_policy and diagnostics are required")
    diagnostics: list[FailureDiagnostic] = []
    for item in diagnostics_data:
        if not isinstance(item, dict) or set(item) - {
            "kind",
            "failures",
            "success_counterexamples",
        }:
            raise OfflineRSIError("diagnostics contain unsupported fields")
        try:
            diagnostics.append(FailureDiagnostic(**item))
        except TypeError as exc:
            raise OfflineRSIError("malformed diagnostic") from exc
    return RetrievalPolicy.from_dict(current), tuple(diagnostics)


def run() -> dict[str, Any]:
    """调用模型生成受限候选，结果只是建议且不会自动晋级。"""
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    service = os.environ.get("DMSA_RSI_SERVICE_URL", "")
    if service:
        import httpx

        if len(raw) > _MAX_INPUT_BYTES:
            raise OfflineRSIError("input exceeds the 64 KiB limit")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {"user_id"}:
                raise ValueError("scope required")
            response = httpx.post(
                service + "/rsi/evolve",
                json=payload,
                headers={"Authorization": "Bearer " + os.environ.get("DMSA_MEMORY_API_KEY", "")},
                timeout=180,
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise OfflineRSIError("unified RSI request failed") from exc
    policy, diagnostics = _parse_payload(raw)
    proposer = ModelAssistedPolicyProposer.from_environment()
    try:
        advice = proposer.propose(policy, diagnostics, development_only=True)
        return {
            "status": "advisory_only",
            "requires_replay_gate": True,
            "proposals": [
                {
                    "candidate_index": item.candidate_index,
                    "search_policy": asdict(item.policy.search),
                    "rationale": item.rationale,
                }
                for item in advice
            ],
        }
    finally:
        proposer.close()


def main() -> int:
    """读取 stdin JSON 并输出建议；错误响应不含模型服务原文。"""
    try:
        result = run()
    except OfflineRSIError as exc:
        sys.stderr.write(f"RSI proposal failed: {exc.message}\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
