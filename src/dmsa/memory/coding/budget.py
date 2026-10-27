"""检索与离线回放共用的保守文本预算."""

from __future__ import annotations

import re

from dmsa.memory.coding.models import CodingMemoryValidationError

_CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    """近似估算模型 token 数; 是保守代理, 不是指定 tokenizer 的精确计数."""
    if not isinstance(text, str):
        raise CodingMemoryValidationError("text must be text")
    try:
        payload = text.encode("utf-8")
    except UnicodeError as exc:
        raise CodingMemoryValidationError("text must be valid Unicode") from exc
    # CJK 每字约 1 token (3 字节), ASCII 与代码约 4 字节 1 token;
    # (字节数 + CJK 字数) / 4 同时覆盖两类, 且空串返回 0。
    cjk = len(_CJK_RE.findall(text))
    return (len(payload) + cjk + 3) // 4
