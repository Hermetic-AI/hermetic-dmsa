"""模型辅助 RSI 的边界与回退测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer
from dmsa.gradient.policy import (
    FailureDiagnostic,
    OfflineRSIError,
    RetrievalPolicy,
)
from dmsa.gradient.systemone import SystemOneDecisionClient


class _ChatClient:
    """可记录请求并返回固定 JSON/文本的轻量模型替身。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_model_advisor_selects_only_deterministic_candidates() -> None:
    """模型只返回索引，策略值仍来自静态 proposal 并可供 replay gate 验证。"""
    decision = _ChatClient('{"candidate_indices":[0]}')
    text = _ChatClient("可提高精确命中权重。")
    advisor = ModelAssistedPolicyProposer(decision, text)
    result = advisor.propose(
        RetrievalPolicy(),
        (FailureDiagnostic("exact_miss", 2),),
        development_only=True,
    )
    assert len(result) == 1
    assert result[0].policy.search.exact_weight == 1.25
    assert result[0].rationale == "可提高精确命中权重。"
    assert decision.calls[0]["temperature"] == 0
    assert "candidate_indices" in decision.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "response",
    ["not json", '{"candidate_indices":[99]}', '{"candidate_indices":[true]}'],
)
def test_model_advisor_rejects_invalid_selection(response: str) -> None:
    """格式错误、越界索引和 bool 不能变成策略参数。"""
    advisor = ModelAssistedPolicyProposer(_ChatClient(response), _ChatClient("rationale"))
    with pytest.raises(OfflineRSIError, match="invalid candidate selection"):
        advisor.propose(
            RetrievalPolicy(),
            (FailureDiagnostic("exact_miss", 2),),
            development_only=True,
        )


def test_model_advisor_does_not_call_models_without_rule_candidates() -> None:
    """缺少可重复失败证据时不消耗模型调用。"""
    decision = _ChatClient("invalid")
    text = _ChatClient("unused")
    advisor = ModelAssistedPolicyProposer(decision, text)
    assert (
        advisor.propose(
            RetrievalPolicy(),
            (FailureDiagnostic("exact_miss", 1),),
            development_only=True,
        )
        == ()
    )
    assert decision.calls == []
    assert text.calls == []


def test_systemone_uses_choice_protocol_and_raw_authorization() -> None:
    """SystemOne 使用给定完整 URL、裸 key 和 answers.candidate.choice。"""
    import httpx

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"answers": {"candidate": {"choice": "1"}}})

    transport = httpx.MockTransport(respond)
    client = SystemOneDecisionClient(
        "https://example.com/compatible-mode/v1/systemone", "secret", "decision-model-preview"
    )
    client._client.close()
    client._client = httpx.Client(transport=transport)
    events: list[dict[str, Any]] = []
    selected = client.select_candidate(
        [{"index": 0}, {"index": 1}], [{"kind": "exact_miss", "failures": 2}], events.append
    )
    assert selected == 1
    assert str(requests[0].url) == "https://example.com/compatible-mode/v1/systemone"
    assert requests[0].headers["Authorization"] == "secret"
    payload = __import__("json").loads(requests[0].content)
    assert payload["model"] == "decision-model-preview"
    assert payload["questions"]["candidate"]["type"] == "choice"
    assert payload["questions"]["candidate"]["criteria"]["1"] == '{"index": 1}'
    assert events[-1]["status"] == "succeeded"
    assert events[-1]["http_status"] == 200
    client.close()


def test_systemone_rejects_out_of_range_choice() -> None:
    """SystemOne 即使返回 200，也不能把候选范围外的值送入策略层。"""
    import httpx

    client = SystemOneDecisionClient(
        "https://example.com/systemone", "secret", "decision-model-preview"
    )
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"answers": {"candidate": {"choice": "9"}}})
        )
    )
    with pytest.raises(OfflineRSIError, match="invalid candidate selection"):
        client.select_candidate([{"index": 0}], [])
    client.close()
