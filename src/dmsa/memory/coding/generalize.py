"""把带具体路径与符号的经验泛化成可跨仓库复用的规则.

泛化只做确定性的文本替换: 正文里出现的具体路径换成 ``<path>``, 具体符号
换成 ``<symbol>``. 替换之后仍然能在正文里找到的具体标识算作泄漏, 这类规则
不允许迁移到别的仓库, 因为那里的路径与符号指向完全不同的代码.

分组口径是"泛化后正文完全相同": 同一条经验在多次会话里换了具体文件, 泛化
后应当落到同一个规则键上, 从而被合并成一条; 正文不同的经验不会因为共享
某个关键词而被并到一起.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# 泛化后用于占位的记号.
PATH_PLACEHOLDER = "<path>"
SYMBOL_PLACEHOLDER = "<symbol>"

# 参与抽象的最短标识长度: 更短的路径或符号视为通用词, 既不抽象也不计泄漏.
MIN_ABSTRACT_LENGTH = 3

# 泛化后仍然留在正文里的仓库专有标识: 文件名与代码符号.
# 文件名要求扩展名至少两个字符, 避免把 "e.g" 这类缩写当成文件.
_FILE_RE = re.compile(r"(?<![\w.-])(?:[\w-]+/)*[\w-]+\.[A-Za-z][A-Za-z0-9]{1,5}(?![\w])")
_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*(?![A-Za-z0-9_])"
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SourceSlice:
    """参与泛化的一条来源切片."""

    id: str
    text: str
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    outcome: str = "unknown"


@dataclass(frozen=True)
class Generalization:
    """一次泛化的结果: 抽象后的规则、规则键与仍然泄漏的标识."""

    text: str
    rule_key: str
    leaked: tuple[str, ...]
    paths: tuple[str, ...]
    symbols: tuple[str, ...]

    @property
    def transferable(self) -> bool:
        """规则是否不含仓库专有标识, 可以迁移到别的仓库."""
        return not self.leaked

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的泛化视图."""
        return {
            "rule": self.text,
            "rule_key": self.rule_key,
            "leaked": list(self.leaked),
            "transferable": self.transferable,
        }


@dataclass(frozen=True)
class RuleGroup:
    """一组共享同一条泛化规则的来源切片."""

    rule_key: str
    rule: str
    source_ids: tuple[str, ...]
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    leaked: tuple[str, ...]
    outcome: str = "unknown"

    @property
    def transferable(self) -> bool:
        """整组规则是否可以迁移到别的仓库."""
        return not self.leaked

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的规则组视图."""
        return {
            "rule_key": self.rule_key,
            "rule": self.rule,
            "sources": list(self.source_ids),
            "paths": list(self.paths),
            "symbols": list(self.symbols),
            "leaked": list(self.leaked),
            "transferable": self.transferable,
            "outcome": self.outcome,
        }


def rule_key(text: str) -> str:
    """返回泛化正文的稳定规则键; 相同规则得到相同键."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    """把连续空白压成单个空格, 保证同一规则得到同一个键."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _abstract_paths(text: str, paths: Sequence[str]) -> str:
    """按长度倒序把具体路径替换成占位记号; 长路径先替换, 避免被短路径截断."""
    body = text
    for path in sorted(paths, key=len, reverse=True):
        if len(path) < MIN_ABSTRACT_LENGTH:
            continue
        body = body.replace(path, PATH_PLACEHOLDER)
    return body


def _abstract_symbols(text: str, symbols: Sequence[str]) -> str:
    """把独立出现的符号替换成占位记号.

    边界只排除 ASCII 标识符字符, 中文正文里紧贴符号的写法同样算独立出现.
    """
    body = text
    for symbol in sorted(symbols, key=len, reverse=True):
        if len(symbol) < MIN_ABSTRACT_LENGTH:
            continue
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])"
        body = re.sub(pattern, SYMBOL_PLACEHOLDER, body)
    return body


def _leaked_terms(text: str, paths: Sequence[str], symbols: Sequence[str]) -> tuple[str, ...]:
    """返回仍然出现在泛化正文里的仓库专有标识.

    判定只看正文本身: 登记过的路径与符号已被抽象, 剩下的文件名与代码符号
    都指向具体实现, 因此这些规则不能原样搬到别的仓库. 登记过但没能抽象的
    标识也一并列出, 便于判断规则还剩多少仓库专属信息.
    """
    found: list[str] = []

    def remember(value: str) -> None:
        if value not in found:
            found.append(value)

    for pattern in (_FILE_RE, _SYMBOL_RE):
        for match in pattern.findall(text):
            remember(match)
    for path in paths:
        if len(path) >= MIN_ABSTRACT_LENGTH and path in text:
            remember(path)
    for symbol in symbols:
        if len(symbol) >= MIN_ABSTRACT_LENGTH and symbol in text:
            remember(symbol)
    return tuple(found)


def generalize(text: str, paths: Iterable[str] = (), symbols: Iterable[str] = ()) -> Generalization:
    """把正文里的具体路径与符号抽象成占位记号.

    泛化只改写正文, 不改写规则含义: 剩下的自然语言部分原样保留, 因此
    抽象后的规则仍能被同样的自然语言查询命中.
    """
    clean_paths = tuple(dict.fromkeys(p.strip() for p in paths if isinstance(p, str) and p.strip()))
    clean_symbols = tuple(
        dict.fromkeys(s.strip() for s in symbols if isinstance(s, str) and s.strip())
    )
    body = _abstract_paths(text, clean_paths)
    body = _abstract_symbols(body, clean_symbols)
    body = _normalize(body)
    return Generalization(
        body,
        rule_key(body),
        _leaked_terms(body, clean_paths, clean_symbols),
        clean_paths,
        clean_symbols,
    )


def group_rules(slices: Sequence[SourceSlice]) -> tuple[RuleGroup, ...]:
    """按泛化规则把来源切片分组, 组内成员越多说明该规则重复出现得越频繁."""
    buckets: dict[str, list[SourceSlice]] = {}
    rules: dict[str, str] = {}
    leaked: dict[str, list[str]] = {}
    for item in slices:
        result = generalize(item.text, item.paths, item.symbols)
        buckets.setdefault(result.rule_key, []).append(item)
        rules.setdefault(result.rule_key, result.text)
        merged = leaked.setdefault(result.rule_key, [])
        for term in result.leaked:
            if term not in merged:
                merged.append(term)
    groups: list[RuleGroup] = []
    for key, members in buckets.items():
        outcomes = {member.outcome for member in members}
        outcome = outcomes.pop() if len(outcomes) == 1 else "unknown"
        groups.append(
            RuleGroup(
                rule_key=key,
                rule=rules[key],
                source_ids=tuple(member.id for member in members),
                paths=tuple(dict.fromkeys(path for member in members for path in member.paths)),
                symbols=tuple(
                    dict.fromkeys(symbol for member in members for symbol in member.symbols)
                ),
                leaked=tuple(leaked[key]),
                outcome=outcome,
            )
        )
    groups.sort(key=lambda group: (-len(group.source_ids), group.rule_key))
    return tuple(groups)
