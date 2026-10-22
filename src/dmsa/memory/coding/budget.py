"""检索与离线回放共用的保守文本预算."""

from dmsa.memory.coding.models import CodingMemoryValidationError


def estimate_tokens(text: str) -> int:
    """按 UTF-8 字节计量预算; 是保守上界代理, 不是真实模型 tokenizer."""
    if not isinstance(text, str):
        raise CodingMemoryValidationError("text must be text")
    try:
        return len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise CodingMemoryValidationError("text must be valid Unicode") from exc
