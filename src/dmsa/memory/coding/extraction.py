"""从原始轨迹生成可追溯、保守的确定性证据切片。."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmsa.memory.coding.code_anchors import CodeAnchor, extract_anchors
from dmsa.memory.coding.models import CodingMemoryValidationError, SourceMessage

CHUNK_SIZE = 1800
OVERLAP = 160
PATH_RE = re.compile(r"(?<![\w])(?:[\w.-]+/)+[\w.-]+(?:\.[\w.-]+)?")
SYMBOL_RE = re.compile(
    r"\b(?:"
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"  # snake_case
    r"|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+"  # PascalCase
    r"|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+"  # CONSTANT_CASE
    r"|[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*"  # camelCase
    r"|[A-Za-z_][A-Za-z0-9_]*\d[A-Za-z0-9_]*"  # 含数字的标识符
    r")\b"
)
DEFINITION_RE = re.compile(
    r"\b(?:def|class|function|func|fn|struct|interface|enum)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
ERROR_RE = re.compile(
    r"(?:Traceback \(most recent call last\)|"
    r"\b[A-Za-z_][\w.]*Error:\s|\b[A-Za-z_][\w.]*Exception:\s|"
    r"^\s*FAILED(?:\s|:)|未通过|测试失败)",
    re.I | re.M,
)
PASS_RE = re.compile(r"(?:\bpassed\b|\bPASS\b|tests? passed|全部通过|验证通过|测试通过)", re.I)
NEGATED_PASS_RE = re.compile(
    r"\b(?:not|never|did(?:n't| not)|hasn't|have not|wasn't|were not)\s+"
    r"(?:\w+\s+){0,2}pass(?:ed)?\b|(?:未|没有|并不|尚未|并未)(?:全部)?通过",
    re.I,
)


@dataclass(frozen=True)
class Chunk:
    """来源消息的有界字符切片及确定性特征。."""

    message_index: int
    start: int
    end: int
    text: str
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    outcome: str
    anchors: tuple[CodeAnchor, ...] = ()


def extract(
    messages: tuple[SourceMessage, ...],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> list[Chunk]:
    """按消息与字符窗口切片, 保留原文偏移和保守结果标签."""
    if type(chunk_size) is not int or chunk_size < 200:
        raise CodingMemoryValidationError("chunk_size must be an integer of at least 200")
    if type(overlap) is not int or overlap < 0 or overlap >= chunk_size:
        raise CodingMemoryValidationError("overlap must be smaller than chunk_size")
    chunks: list[Chunk] = []
    for index, message in enumerate(messages):
        content = message.content
        start = 0
        while start < len(content):
            end = min(start + chunk_size, len(content))
            if end < len(content):
                boundary = content.rfind("\n", start + chunk_size // 2, end)
                if boundary > start:
                    end = boundary + 1
            text = content[start:end]
            outcome = "unknown"
            if ERROR_RE.search(text):
                outcome = "observed_failure"
            elif PASS_RE.search(text) and not NEGATED_PASS_RE.search(text):
                outcome = "reported_success"
            chunks.append(
                Chunk(
                    index,
                    start,
                    end,
                    text,
                    tuple(dict.fromkeys(PATH_RE.findall(text))),
                    tuple(dict.fromkeys((*SYMBOL_RE.findall(text), *DEFINITION_RE.findall(text)))),
                    outcome,
                    extract_anchors(text),
                )
            )
            if end == len(content):
                break
            start = max(start + 1, end - overlap)
    return chunks
