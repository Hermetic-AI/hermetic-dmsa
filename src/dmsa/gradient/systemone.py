"""专用于阿里云 SystemOne 决策接口的有界候选选择客户端。."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from dmsa.gradient.policy import OfflineRSIError


class SystemOneDecisionClient:
    """调用 SystemOne choice 问题, 只返回本地候选索引."""

    def __init__(self, endpoint: str, api_key: str, model: str) -> None:
        """验证专用 endpoint 并初始化独立 HTTP 会话."""
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise OfflineRSIError("DMSA_DECISION_BASE_URL must be a credential-free HTTPS URL")
        if not api_key.strip():
            raise OfflineRSIError("DMSA_DECISION_API_KEY is required")
        if not model.strip():
            raise OfflineRSIError("DMSA_DECISION_MODEL is required")
        try:
            import httpx
        except ImportError as exc:
            raise OfflineRSIError(
                "install hermetic-dmsa[http] for SystemOne decision calls"
            ) from exc
        self.endpoint = endpoint
        self.api_key = api_key.strip()
        self.model = model.strip()
        self._client = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)

    def select_candidate(
        self,
        candidates: list[dict[str, object]],
        diagnostics: list[dict[str, object]],
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> int:
        """以官方 choice schema 请求选择并严格验证返回的候选索引."""
        if not candidates:
            raise OfflineRSIError("candidate list must not be empty")
        criteria = {
            str(index): json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            for index, candidate in enumerate(candidates)
        }
        payload = {
            "model": self.model,
            "state": {"diagnostics": diagnostics, "candidates": candidates},
            "questions": {
                "candidate": {
                    "type": "choice",
                    "instructions": (
                        "Select the candidate index that best addresses the diagnostics. "
                        "Choose only from the listed candidates."
                    ),
                    "criteria": criteria,
                }
            },
        }
        started = time.monotonic()
        _observe(observer, {"model": self.model, "status": "started"})
        try:
            response = self._client.post(
                self.endpoint,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            status = response.status_code
            response.raise_for_status()
            body = response.json()
            answers = body.get("answers") if isinstance(body, dict) else None
            candidate = answers.get("candidate") if isinstance(answers, dict) else None
            choice = candidate.get("choice") if isinstance(candidate, dict) else None
            if not isinstance(choice, (str, int)) or isinstance(choice, bool):
                raise ValueError("missing choice")
            index = int(choice)
            if str(index) != str(choice) or not 0 <= index < len(candidates):
                raise ValueError("choice is not a listed candidate index")
        except Exception as exc:
            error_response = getattr(exc, "response", None)
            http_status = (
                error_response.status_code
                if error_response is not None
                else locals().get("status")
            )
            _observe(
                observer,
                {
                    "model": self.model,
                    "status": "failed",
                    "http_status": http_status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise OfflineRSIError("decision model returned an invalid candidate selection") from exc
        _observe(
            observer,
            {
                "model": self.model,
                "status": "succeeded",
                "http_status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return index

    def close(self) -> None:
        """关闭 HTTP 会话并尽量清理实例上的密钥引用."""
        self._client.close()
        self.api_key = ""


def _observe(
    observer: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]
) -> None:
    """报告去敏后的模型调用事件, 观察器异常不能影响提案逻辑."""
    if observer is not None:
        with suppress(Exception):
            observer(event)
