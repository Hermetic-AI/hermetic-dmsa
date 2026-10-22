"""将单个来源切片映射为可解释的工程经验视图。."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmsa.memory.coding.extraction import Chunk

_BUG_RE = re.compile(r"\b(?:bug|defect|regression|crash|exception|error|fails?)\b", re.I)
_FEATURE_RE = re.compile(r"\b(?:feature|enhancement|feature request|support for)\b", re.I)
_SYMPTOM_RE = re.compile(
    r"\b(?:bug|symptom|issue|fails?|failing|raises?|throws?)\s*[:\uFF1A-]?\s*(.*)", re.I
)
_CAUSE_RE = re.compile(r"\b(?:root cause|cause|because|due to)\s*[:\uFF1A-]?\s*(.*)", re.I)
_ACTION_RE = re.compile(
    r"\b(?:fix|change|implementation|implement|solution|updated?)\s*[:\uFF1A-]?\s*(.*)", re.I
)
_FAILED_RE = re.compile(
    r"\b(?:failed|failure|did not work|does not work|regressed)\s*[:\uFF1A-]?\s*(.*)", re.I
)
_VALIDATION_RE = re.compile(r"\b(?:pytest|tests?\b|passed|coverage|lint|mypy|ruff)\b", re.I)
_CONDITION_RE = re.compile(
    r"\b(?:compatibility|precondition|only when|requires?|if|unless|default|version)"
    r"\s*[:\uFF1A-]?\s*(.*)",
    re.I,
)


@dataclass(frozen=True)
class ExperienceCard:
    """保留原始切片来源的轻量工程经验卡片。."""

    kind: str
    message_index: int
    source_start: int
    source_end: int
    outcome: str
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    symptom: str
    root_cause: str
    action: str
    failed_attempts: str
    validation: str
    preconditions: tuple[str, ...]
    evidence: tuple[str, ...]


def extract_experience_card(chunk: Chunk) -> ExperienceCard:
    """从原文显式提示提取槽位; 不推断缺失事实或执行内容。."""
    lines = tuple(line.strip() for line in chunk.text.splitlines() if line.strip())
    symptom = _first_match(lines, _SYMPTOM_RE)
    if not symptom:
        symptom = _first_match(lines, re.compile(r"\bfeature request\s*[:\uFF1A-]?\s*(.*)", re.I))
    cause = _first_match(lines, _CAUSE_RE)
    action = _first_match(lines, _ACTION_RE)
    failed = _first_match(lines, _FAILED_RE)
    validation = _first_match(lines, _VALIDATION_RE, full_line=True)
    conditions = _all_matches(lines, _CONDITION_RE)
    has_bug = bool(_BUG_RE.search(chunk.text))
    has_feature = bool(_FEATURE_RE.search(chunk.text))
    kind = "bug_fix" if has_bug and not has_feature else "feature_enhancement"
    if has_bug and has_feature:
        kind = "bug_fix" if symptom or cause else "feature_enhancement"
    if not has_bug and not has_feature:
        kind = "unknown"
    facts = tuple(
        dict.fromkeys(
            item for item in (symptom, cause, action, failed, validation, *conditions) if item
        )
    )
    if not facts:
        facts = (chunk.text,)
    return ExperienceCard(
        kind=kind,
        message_index=chunk.message_index,
        source_start=chunk.start,
        source_end=chunk.end,
        outcome=chunk.outcome,
        paths=chunk.paths,
        symbols=chunk.symbols,
        symptom=symptom,
        root_cause=cause,
        action=action,
        failed_attempts=failed,
        validation=validation,
        preconditions=conditions,
        evidence=facts,
    )


def applicability_adjustment(query: str, card: ExperienceCard) -> float:
    """按明确匹配的经验类型轻调排序, 不替代通道分数。."""
    query_has_bug = bool(_BUG_RE.search(query))
    query_has_feature = bool(_FEATURE_RE.search(query))
    query_requests_failure = bool(
        re.search(
            r"\b(?:failed|failure|did not work|does not work|regression|counterexample)\b",
            query,
            re.I,
        )
    )
    adjustment = 0.0
    matching_bug = card.kind == "bug_fix" and query_has_bug and not query_has_feature
    matching_feature = (
        card.kind == "feature_enhancement" and query_has_feature and not query_has_bug
    )
    if matching_bug or matching_feature:
        adjustment += 0.004
    elif card.kind in {"bug_fix", "feature_enhancement"} and (query_has_bug or query_has_feature):
        adjustment -= 0.002
    if card.failed_attempts:
        adjustment += 0.003 if query_requests_failure else -0.002
    return adjustment


def _first_match(
    lines: tuple[str, ...], pattern: re.Pattern[str], *, full_line: bool = False
) -> str:
    """返回第一个显式匹配来源行; 不生成摘要。."""
    for line in lines:
        match = pattern.search(line)
        if match:
            return line if full_line else (match.group(1).strip() or line)
    return ""


def _all_matches(lines: tuple[str, ...], pattern: re.Pattern[str]) -> tuple[str, ...]:
    """返回去重后的显式条件来源行。."""
    values = []
    for line in lines:
        match = pattern.search(line)
        if match:
            values.append(match.group(1).strip() or line)
    return tuple(dict.fromkeys(values))
