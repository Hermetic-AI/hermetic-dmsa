"""从原始轨迹生成可追溯、保守的确定性证据切片。."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmsa.memory.coding.models import SourceMessage

CHUNK_SIZE = 1800
OVERLAP = 160
PATH_RE = re.compile(r"(?<![\w])(?:[\w.-]+/)+[\w.-]+(?:\.[\w.-]+)?")
SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
ERROR_RE = re.compile(r"(?:Traceback|\bError\b|\bException\b|FAILED|\bfailed\b)", re.I)
PASS_RE = re.compile(r"(?:\bpassed\b|\bPASS\b|tests? passed)", re.I)


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


def extract(messages: tuple[SourceMessage, ...]) -> list[Chunk]:
    """按消息与固定字符窗口切片, 保留原文偏移和保守结果标签。."""
    chunks: list[Chunk] = []
    for index, message in enumerate(messages):
        content = message.content
        start = 0
        while start < len(content):
            end = min(start + CHUNK_SIZE, len(content))
            if end < len(content):
                boundary = content.rfind("\n", start + CHUNK_SIZE // 2, end)
                if boundary > start:
                    end = boundary + 1
            text = content[start:end]
            outcome = "unknown"
            if ERROR_RE.search(text):
                outcome = "observed_failure"
            elif PASS_RE.search(text) and message.role.lower() in {"tool", "system"}:
                outcome = "reported_success"
            chunks.append(
                Chunk(
                    index,
                    start,
                    end,
                    text,
                    tuple(dict.fromkeys(PATH_RE.findall(text))),
                    tuple(dict.fromkeys(SYMBOL_RE.findall(text))),
                    outcome,
                )
            )
            if end == len(content):
                break
            start = max(start + 1, end - OVERLAP)
    return chunks
