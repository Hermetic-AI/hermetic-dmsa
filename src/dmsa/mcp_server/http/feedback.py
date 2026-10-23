# ruff: noqa: D415, RUF002
"""集成 RSI 的认证 HTTP 契约。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI


class ScopeContract(BaseModel):
    """限定调用的项目范围。"""

    model_config = ConfigDict(extra="forbid")
    user_id: StrictStr = Field(min_length=1, max_length=256)


class FeedbackContract(ScopeContract):
    """反馈包含显式诊断和人工或 Agent 标注的相关来源。"""

    trace_id: StrictStr = Field(min_length=1, max_length=64)
    outcome: Literal["success", "failure"]
    kind: Literal["unknown", "exact_miss", "redundancy", "missing_context"] = "unknown"
    relevant_ids: list[StrictStr] = Field(default_factory=list, max_length=100)


class RollbackContract(ScopeContract):
    """选择已记录的策略版本。"""

    version: StrictStr = Field(min_length=64, max_length=64)


def install_feedback(app: FastAPI, rsi: IntegratedRSI, authenticate: Any) -> None:
    """复用记忆 API 的认证，反馈成功后同步执行一次有界进化。"""

    @app.post("/feedback", dependencies=[Depends(authenticate)])
    def feedback(payload: FeedbackContract) -> dict[str, Any]:
        """幂等保存反馈，新反馈触发闭环；模型错误不丢失已保存的反馈。"""
        store = RetrievalFeedbackStore(rsi.memory.store.path)
        try:
            changed = store.feedback(
                payload.user_id,
                payload.trace_id,
                payload.outcome,
                payload.kind,
                payload.relevant_ids,
            )
            store.event(
                payload.user_id,
                "feedback_saved",
                {"trace_id": payload.trace_id, "outcome": payload.outcome, "kind": payload.kind},
            )
        finally:
            store.close()
        return {
            "stored": True,
            "evolution": rsi.evolve(payload.user_id)
            if changed
            else {"status": "duplicate_feedback"},
        }

    @app.post("/rsi/propose", dependencies=[Depends(authenticate)])
    @app.post("/rsi/evolve", dependencies=[Depends(authenticate)])
    def evolve(payload: ScopeContract) -> dict[str, Any]:
        """重试当前 scope 的提案、回放和门禁激活。"""
        return rsi.evolve(payload.user_id)

    @app.post("/rsi/rollback", dependencies=[Depends(authenticate)])
    def rollback(payload: RollbackContract) -> dict[str, bool]:
        """恢复已验证版本，下一次检索立即读取新活动版本。"""
        rsi.rollback(payload.user_id, payload.version)
        return {"success": True}
