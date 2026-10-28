"""Coding Memory HTTP adapter。"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dmsa.errors import DMSAError, InvalidConfigValueError
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.policy import OfflineRSIError
from dmsa.mcp_server.http.contracts import AddContract, SearchContract, validate_message
from dmsa.mcp_server.http.feedback import install_feedback
from dmsa.memory import CodingMemory, CodingMemoryConflictError, CodingMemoryValidationError


def create_app(memory: CodingMemory, api_key: str, rsi: IntegratedRSI | None = None) -> FastAPI:
    """为调用方拥有的 memory 创建 bearer/token/key 认证 HTTP app。"""
    if not isinstance(api_key, str) or not api_key.strip():
        raise InvalidConfigValueError("api_key must be nonempty")
    app = FastAPI(title="DMSA Coding Memory", docs_url=None, redoc_url=None)

    @app.exception_handler(sqlite3.Error)
    async def database_error(_: Any, __: sqlite3.Error) -> JSONResponse:
        """数据库故障返回固定响应，不泄露查询及数据库路径。"""
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})

    @app.exception_handler(OfflineRSIError)
    async def rsi_error(_: Any, __: OfflineRSIError) -> JSONResponse:
        """隐藏模型响应与内部数据，审计库保留错误类型。"""
        return JSONResponse(status_code=422, content={"detail": "RSI request failed"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Any, __: RequestValidationError) -> JSONResponse:
        """回传固定错误体，避免 FastAPI 将输入内容写进响应。"""
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    def authenticate(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> None:
        """检查令牌，不将凭据写入日志或响应。"""
        supplied = x_api_key
        if authorization:
            parts = authorization.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() in {"bearer", "token"}:
                supplied = parts[1]
        if supplied is None or not secrets.compare_digest(
            supplied.encode("utf-8"), api_key.encode("utf-8")
        ):
            raise HTTPException(
                status_code=401,
                detail="unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    def health() -> dict[str, bool]:
        """无认证存活探针。"""
        return {"ok": True}

    @app.post("/add", dependencies=[Depends(authenticate)])
    def add(payload: dict[str, Any]) -> dict[str, Any]:
        """验证并幂等耐久写入来源。"""
        try:
            request = AddContract.model_validate(payload)
            messages = [validate_message(message) for message in request.messages]
            memory.add(
                request.request_id,
                request.user_id,
                request.session_id,
                messages,
                repo=request.repo,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid request") from exc
        except CodingMemoryValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid request") from exc
        except CodingMemoryConflictError as exc:
            raise HTTPException(status_code=409, detail="request conflict") from exc
        except DMSAError as exc:
            raise HTTPException(status_code=503, detail="memory request failed") from exc
        return {
            "success": True,
            "request_id": request.request_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "repo": request.repo,
        }

    @app.post("/search", dependencies=[Depends(authenticate)])
    def search(payload: dict[str, Any]) -> dict[str, Any]:
        """返回限定 user scope 的检索结果。"""
        try:
            request = SearchContract.model_validate(payload)
            if rsi is not None:
                return rsi.search(
                    request.query,
                    request.user_id,
                    request.top_k,
                    task_id=request.task_id,
                    repo=request.repo,
                )
            results = memory.search(
                request.query, request.user_id, request.top_k, repo=request.repo
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid request") from exc
        except CodingMemoryValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid request") from exc
        except DMSAError as exc:
            raise HTTPException(status_code=503, detail="memory request failed") from exc
        return {
            "data": [{"id": hit.id, "content": hit.content, "score": hit.score} for hit in results]
        }

    if rsi is not None:
        install_feedback(app, rsi, authenticate)
    return app
