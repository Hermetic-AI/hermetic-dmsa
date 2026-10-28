"""程序性记忆: 把「怎么做」沉淀成可复用的步骤流程.

与事实型记忆的区别: 事实型记忆回答「发生过什么」, 程序性记忆回答「下次该按
什么顺序做」. 程序由确定性规则从会话里抽取, 只认显式的有序步骤、前置条件
与验证说明, 不推断未写出的步骤.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from dmsa.memory.coding.models import SourceMessage

# 步骤行: 数字编号或常见列表符号开头.
_STEP_RE = re.compile(r"^\s*(?:\d+[.)、]|[(（]\d+[)）]|[-*•])\s*(\S.*)$")
# 前置条件行.
_PRECONDITION_RE = re.compile(
    r"(?:前置|前提|先决|前置条件|适用条件|要求|仅当|precondition|prerequisite|requires|only when)",
    re.I,
)
# 验证行.
_VERIFICATION_RE = re.compile(
    r"(?:验证|校验|检查|测试|确认|verify|validation|pytest|\btests?\b)", re.I
)
# 标题行: Markdown 标题或「步骤/流程/步骤如下」引导句.
_TITLE_RE = re.compile(r"^\s*#{1,6}\s+(?P<title>\S.*)$")
# 中文词段切分: CJK 连续段与非 CJK 段.
_CJK_RANGES = "㐀-䶿一-鿿぀-ヿ가-힯"
_RUN_RE = re.compile(f"[{_CJK_RANGES}]+|[^\\s{_CJK_RANGES}]+")
MAX_STEPS = 40
MAX_TEXT = 400


@dataclass(frozen=True)
class Procedure:
    """一条可复用的操作流程."""

    title: str
    steps: tuple[str, ...]
    preconditions: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    trigger_terms: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    outcome: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的流程视图."""
        return {
            "title": self.title,
            "steps": list(self.steps),
            "preconditions": list(self.preconditions),
            "verification": list(self.verification),
            "trigger_terms": list(self.trigger_terms),
            "paths": list(self.paths),
            "outcome": self.outcome,
        }


def _cjk_grams(text: str) -> list[str]:
    """产出中文 2 gram 与 3 gram, 让两字短查询也能精确命中."""
    grams: list[str] = []
    for token in text.split():
        for run in _RUN_RE.findall(token):
            if not _is_cjk(run):
                continue
            for size in (2, 3):
                for index in range(len(run) - size + 1):
                    gram = run[index : index + size]
                    if gram not in grams:
                        grams.append(gram)
    return grams


def _is_cjk(run: str) -> bool:
    """判断词段是否由中文/假名/谚文组成."""
    return bool(run) and bool(re.fullmatch(f"[{_CJK_RANGES}]+", run))


def _clean(line: str) -> str:
    """去掉列表符号并裁剪长度."""
    match = _STEP_RE.match(line)
    text = match.group(1).strip() if match else line.strip()
    return text[:MAX_TEXT]


def _title(messages: tuple[SourceMessage, ...]) -> str:
    """取标题: 优先 Markdown 标题, 其次首条用户消息首行."""
    for message in messages:
        for line in message.content.splitlines():
            match = _TITLE_RE.match(line)
            if match:
                return match.group("title").strip()[:MAX_TEXT]
    for message in messages:
        if message.role != "user":
            continue
        for line in message.content.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:MAX_TEXT]
    return ""


def _trigger_text(messages: tuple[SourceMessage, ...]) -> str:
    """取触发语境的文本: 首条用户消息."""
    for message in messages:
        if message.role == "user":
            return message.content
    return ""


def build_procedure(messages: tuple[SourceMessage, ...]) -> Procedure | None:
    """从有序会话消息抽取流程; 没有显式有序步骤时返回 None."""
    steps: list[str] = []
    preconditions: list[str] = []
    verification: list[str] = []
    for message in messages:
        for line in message.content.splitlines():
            if not line.strip():
                continue
            if _STEP_RE.match(line):
                steps.append(_clean(line))
                continue
            stripped = line.strip()
            if _PRECONDITION_RE.search(stripped):
                preconditions.append(stripped[:MAX_TEXT])
            elif _VERIFICATION_RE.search(stripped):
                verification.append(stripped[:MAX_TEXT])
    if len(steps) < 2:
        return None
    title = _title(messages)
    if not title:
        return None
    trigger = _trigger_text(messages)
    outcome = "reported_success" if verification else "unknown"
    return Procedure(
        title=title,
        steps=tuple(steps[:MAX_STEPS]),
        preconditions=tuple(dict.fromkeys(preconditions)),
        verification=tuple(dict.fromkeys(verification)),
        trigger_terms=(trigger[:MAX_TEXT],) if trigger else (),
        outcome=outcome,
    )


def procedure_terms(procedure: Procedure) -> tuple[str, ...]:
    """返回流程的检索词项: 标题、触发语境与步骤文本."""
    parts = (procedure.title, *procedure.trigger_terms, *procedure.steps)
    terms: list[str] = []
    for part in parts:
        for term in match_terms(part):
            if term not in terms:
                terms.append(term)
    return tuple(terms)


def match_terms(text: str) -> tuple[str, ...]:
    """把文本展开成流程检索词项: ASCII 词元 + 中文 2 gram 与 3 gram.

    中文短于 trigram 窗口的词由 2 gram 覆盖, 因此「缓存」这类两字查询能精确
    命中流程索引, 不必退化成子串匹配.
    """
    from dmsa.memory.coding.tokenize import query_terms

    terms: list[str] = []
    for term in query_terms(text):
        if term not in terms:
            terms.append(term)
    for gram in _cjk_grams(text):
        if gram not in terms:
            terms.append(gram)
    return tuple(terms)
