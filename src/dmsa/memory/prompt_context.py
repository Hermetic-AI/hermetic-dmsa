"""PromptContext 工厂（薄封装 pmd.PromptContext）。

PMD skeleton 已提供 :class:`pmd.PromptContext`。本模块只做：

1. 提供 ``create_prompt_context`` 工厂函数（自动生成 ULID / 校验必填字段）
2. 提供 Pydantic ↔ pmd dataclass 的互转辅助
3. 异常体系（PD4）：``InvalidPromptContextError``

依赖规则：只 import pmd + dmsa.errors。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from pmd import PromptContext as PMDPromptContext

from dmsa.errors import InvalidPromptContextError

__all__ = [
    "PromptContext",
    "create_prompt_context",
    "to_pmd",
    "from_pmd",
    "new_agent_id",
]


def new_agent_id() -> str:
    """生成新的 agent ID（ULID 形式；与 PMD 一致）。"""
    return _new_ulid()


def _new_ulid() -> str:
    """生成一个简单的 ULID-like 字符串（26 字符，时间排序）。

    真正的 ULID 库是 ``python-ulid`` 或 ``ulid-py``，但保持零依赖我们用
    时间戳（10 字符 base32）+ 16 字符随机 base32 的拼接。
    """
    import base64
    import os
    import struct

    # 时间戳部分：48 bit ms timestamp → 10 字符 base32（Crockford）
    ts_ms = int(time.time() * 1000)
    ts_bytes = struct.pack(">Q", ts_ms)[2:]  # 6 字节
    # 简化：直接转 hex，避免 base32 padding
    ts_part = ts_bytes.hex().upper()  # 12 字符
    # 随机部分
    rand_bytes = os.urandom(8)
    rand_part = rand_bytes.hex().upper()  # 16 字符
    return (ts_part + rand_part)[:26]


def create_prompt_context(
    *,
    agent_id: str | None = None,
    system_prompt: str = "",
    user_prompt: str = "",
    parent_prompt_id: str | None = None,
    model_name: str = "",
    temperature: float = 0.0,
    extra: Mapping[str, Any] | None = None,
) -> PMDPromptContext:
    """创建 :class:`pmd.PromptContext`，自动填充必填字段 + 生成 ULID。

    Args:
        agent_id: Agent 唯一 ID；不传则生成新的。
        system_prompt: 系统 prompt 文本。
        user_prompt: 用户 prompt 文本。
        parent_prompt_id: 父 prompt ID（链式调用时填）。
        model_name: LLM 模型名。
        temperature: 温度参数 ``[0, 2]``。
        extra: 附加 metadata（写入 ``tool_calls`` 等）。

    Returns:
        配置好必填字段的 :class:`pmd.PromptContext`。

    Raises:
        InvalidPromptContextError: 必填字段缺失或温度越界。
    """
    if not system_prompt and not user_prompt:
        raise InvalidPromptContextError(
            "at least one of system_prompt / user_prompt must be non-empty",
            details={"system_empty": not system_prompt, "user_empty": not user_prompt},
        )
    if not 0.0 <= temperature <= 2.0:
        raise InvalidPromptContextError(
            f"temperature must be in [0, 2], got {temperature}",
            details={"temperature": temperature},
        )

    prompt_id = _new_ulid()
    aid = agent_id or _new_ulid()
    tool_calls: list[dict[str, Any]] = list(extra.get("tool_calls", [])) if extra else []
    retrieved: list[str] = list(extra.get("retrieved_memory_ids", [])) if extra else []
    few_shot: list[str] = list(extra.get("few_shot_memory_ids", [])) if extra else []

    return PMDPromptContext(
        prompt_id=prompt_id,
        agent_id=aid,
        parent_prompt_id=parent_prompt_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm_response="",  # 调用方可在之后修改
        model_name=model_name,
        temperature=temperature,
        request_timestamp=time.time(),
        response_timestamp=None,
        token_usage={},
        success=True,
        error=None,
        tool_calls=tool_calls,
        retrieved_memory_ids=retrieved,
        few_shot_memory_ids=few_shot,
    )


def to_pmd(prompt_context: PMDPromptContext) -> PMDPromptContext:
    """identity function（占位，供未来 Pydantic 转换用）。"""
    return prompt_context


def from_pmd(prompt_context: PMDPromptContext) -> PMDPromptContext:
    """identity function（占位，供未来 Pydantic 转换用）。"""
    return prompt_context


# 兼容性别名（PD3：公共 API 用统一类型名）
PromptContext = PMDPromptContext
