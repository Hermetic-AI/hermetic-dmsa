"""把整段会话压缩成确定性的工程经验卡: 症状、根因、最终修复与验证."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmsa.memory.coding.extraction import DEFINITION_RE, PATH_RE, SYMBOL_RE
from dmsa.memory.coding.models import SourceMessage

EPISODE_PREFIX = "[episode]"
SUPERSEDED_OUTCOME = "superseded"
MAX_CARD_CHARS = 1800
_PATCH_RE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]+path=([^\s`]+)\n(.*?)```", re.S)
_VALIDATION_RE = re.compile(
    r"(?:全部通过|验证通过|测试通过|tests? passed|\bPASS(?:ED)?\b|pytest[^\n]{0,40}pass)", re.I
)
_NEGATED_RE = re.compile(
    r"(?:未|没有|并不|尚未|并未)(?:全部)?通过|\b(?:FAILED|failing|did not work)\b", re.I
)
_FAILURE_RE = re.compile(r"Traceback|AssertionError|\bFAILED\b|未通过|测试失败|失败|报错", re.I)
_ROOT_CAUSE_RE = re.compile(r"(?:根因|原因|因为|导致|问题在于|due to|root cause|because)", re.I)


@dataclass(frozen=True)
class EpisodeCard:
    """会话级经验卡及其可检索文本."""

    symptom: str
    root_cause: str
    failed_attempts: int
    patch_path: str
    patch_body: str
    validation: str
    outcome: str
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    text: str


def _first_line(text: str, limit: int = 160) -> str:
    """取第一条非空行并截断."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def _patches(messages: tuple[SourceMessage, ...]) -> list[tuple[int, str, str]]:
    """按出现顺序收集带 path 标记的代码块."""
    found: list[tuple[int, str, str]] = []
    for index, message in enumerate(messages):
        for path, body in _PATCH_RE.findall(message.content):
            found.append((index, path.strip(), body.strip("\n")))
    return found


def _validation_index(messages: tuple[SourceMessage, ...]) -> tuple[int, str]:
    """返回最后一次显式验证通过的消息序号与证据行."""
    found_index, found_text = -1, ""
    for index, message in enumerate(messages):
        for line in message.content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _VALIDATION_RE.search(stripped) and not _NEGATED_RE.search(stripped):
                found_index, found_text = index, stripped[:160]
    return found_index, found_text


def _root_cause(messages: tuple[SourceMessage, ...], limit: int) -> str:
    """取第一条显式根因说明."""
    for message in messages[:limit]:
        for line in message.content.splitlines():
            stripped = line.strip()
            if stripped and _ROOT_CAUSE_RE.search(stripped):
                return stripped[:200]
    return ""


def build_episode(messages: tuple[SourceMessage, ...]) -> EpisodeCard | None:
    """从有序会话消息构造经验卡; 没有可复用内容时返回 None."""
    if not messages:
        return None
    patches = _patches(messages)
    validation_index, validation = _validation_index(messages)
    chosen: tuple[int, str, str] | None = None
    for patch in patches:
        if patch[0] <= validation_index:
            chosen = patch
    if chosen is None and patches:
        chosen = patches[-1]
    symptom = ""
    for message in messages:
        if message.role == "user":
            symptom = _first_line(message.content)
            break
    if not symptom:
        symptom = _first_line(messages[0].content)
    root_cause = _root_cause(messages, len(messages))
    failed = sum(1 for message in messages if _FAILURE_RE.search(message.content))
    if chosen is None or validation_index < 0 or chosen[0] > validation_index:
        # 只为"有补丁且后续有显式验证"的会话生成经验卡, 避免把未验证尝试复制成经验。
        return None
    patch_path = chosen[1] if chosen else ""
    patch_body = chosen[2] if chosen else ""
    if validation and chosen is not None:
        outcome = "reported_success"
    elif failed:
        outcome = "observed_failure"
    else:
        outcome = "unknown"
    paths = tuple(
        dict.fromkeys(
            (patch_path, *PATH_RE.findall(f"{root_cause}\n{symptom}"))
            if patch_path
            else PATH_RE.findall(f"{root_cause}\n{symptom}")
        )
    )
    symbols = tuple(
        dict.fromkeys((*SYMBOL_RE.findall(patch_body), *DEFINITION_RE.findall(patch_body)))
    )
    lines = [
        f"{EPISODE_PREFIX} 症状: {symptom}" if symptom else f"{EPISODE_PREFIX}",
        f"根因: {root_cause}" if root_cause else "",
        f"失败尝试: {failed} 次" if failed else "",
        f"修复文件: {patch_path}" if patch_path else "",
        f"验证: {validation}" if validation else "",
    ]
    body_lines = [line for line in lines if line]
    if patch_path and patch_body:
        body_lines.append(f"```python path={patch_path}\n{patch_body}\n```")
    text = "\n".join(body_lines)
    if len(text) > MAX_CARD_CHARS:
        text = text[:MAX_CARD_CHARS] + "\n[truncated]"
    return EpisodeCard(
        symptom=symptom,
        root_cause=root_cause,
        failed_attempts=failed,
        patch_path=patch_path,
        patch_body=patch_body,
        validation=validation,
        outcome=outcome,
        paths=paths,
        symbols=symbols,
        text=text,
    )
