"""按代码证据质量与查询短语重合度调整排序, 不改变召回通道."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from dmsa.memory.coding.episodes import EPISODE_PREFIX

_STALE_WRITE_PREFIX = "[tool_use Write]"
# 只认行首代码结构, 避免英文散文里的 if/for/return 也被当成代码密度。
_CODE_RE = re.compile(
    r"^\s*(?:\d+[\s:\t]+)?(?:async\s+def|def|class)\s+[A-Za-z_]\w*"
    r"|^\s*(?:\d+[\s:\t]+)?"
    r"(?:return|raise|import|from|yield|if|elif|else|for|while|try|except|finally|with)\b"
    r"|^\s*(?:\d+[\s:\t]+)?[A-Za-z_][\w.]*\s*=(?!=)",
    re.M,
)
_ACTION_RE = re.compile(r"diff --git|\[tool_use Edit\]", re.I)
_USEFUL_RE = re.compile(
    r"\b(?:def|class|return|raise|import|from|fix|fixed|root cause|error|exception|"
    r"traceback|failed|failure|passed|pytest|change|changed|update|updated|solution|"
    r"bug|issue)\b",
    re.I,
)
_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_SEPARATOR_RE = re.compile(r"[._/:\-]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[0-9]+")
_CAMEL_DETECT_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2,}[a-z]")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_CJK_GRAM = 3
# 单字中文 (如"的") 几乎命中任何证据, 作为排序特征只会放大噪声。
_CJK_MIN_RUN = 2
_FENCE_PATH_RE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]+path=([^\s`]+)")
_FILE_HINT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    r"|^[A-Za-z0-9_-]+\.(?:py|pyi|ts|tsx|js|jsx|java|go|rs|rb|cs|cpp|cc|h|hpp|"
    r"md|json|ya?ml|toml|ini|cfg|sql|sh|css|html)$",
    re.I,
)
_VALIDATED_RE = re.compile(
    r"\bpytest\b[^\n]{0,40}\b(?:pass(?:ed)?|ok)\b"
    r"|\btests?\s+pass(?:ed)?\b"
    r"|全部通过|验证通过|测试通过",
    re.I,
)
_FAILED_ATTEMPT_RE = re.compile(
    r"旧会话|历史修复尝试|未通过验证|did not work|does not work|failed attempt|regressed", re.I
)
_STOPWORDS = frozenset(
    (
        "the", "and", "for", "with", "this", "that", "from", "into", "not", "are",
        "was", "has", "have", "is", "as", "of", "to", "in", "on", "by", "or", "it",
        "its", "be", "when", "what", "which", "who", "how", "why", "all", "any",
        "can", "could", "should", "would", "does", "did", "do",
    )
)
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
# 低信息密度阈值: 长中文文本里若去重字符占比过低, 多半是重复填充而非可复用经验。
_LOW_DENSITY_LENGTH = 200
_LOW_DENSITY_RATIO = 0.22
_LOW_DENSITY_CJK_SHARE = 0.3


@dataclass(frozen=True)
class QueryFeatures:
    """查询中可用于判断证据质量的短语、标识符片段、文件线索与中文三元组."""

    phrases: frozenset[str]
    identifiers: frozenset[str]
    cjk_grams: frozenset[str]
    paths: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()


def _identifier_parts(word: str) -> tuple[str, ...]:
    """拆分蛇形、点分、路径式和驼峰标识符, 保留长度至少 3 的片段及相邻组合."""
    if not _SEPARATOR_RE.search(word) and not _CAMEL_DETECT_RE.search(word):
        return ()
    parts: list[str] = []
    for chunk in _SEPARATOR_RE.split(word):
        pieces = _CAMEL_RE.findall(chunk)
        for piece in pieces if "".join(pieces) == chunk else (chunk,):
            if len(piece) >= 3:
                parts.append(piece.lower())
    unique = tuple(dict.fromkeys(parts))
    pairs = tuple("_".join(pair) for pair in pairwise(unique))
    return tuple(dict.fromkeys((*unique, *pairs)))


def query_features(query: str) -> QueryFeatures:
    """预计算查询短语与标识符特征, 避免逐条证据重复解析."""
    raw_words = _WORD_RE.findall(query)
    words = [word.lower() for word in raw_words]
    phrases = {
        " ".join(words[index : index + 3]) for index in range(max(0, len(words) - 2))
    }
    identifiers: set[str] = set()
    paths: set[str] = set()
    for word in raw_words:
        identifiers.update(_identifier_parts(word))
        if _FILE_HINT_RE.match(word):
            paths.add(word.lower())
    cjk_grams: set[str] = set()
    for run in _CJK_RUN_RE.findall(query):
        if len(run) < _CJK_MIN_RUN:
            continue
        if len(run) < _CJK_GRAM:
            cjk_grams.add(run)
        else:
            cjk_grams.update(run[index : index + _CJK_GRAM] for index in range(len(run) - 2))
    return QueryFeatures(
        frozenset(phrases),
        frozenset(identifiers),
        frozenset(cjk_grams),
        frozenset(paths),
        frozenset(
            word
            for word in words
            if len(word) >= 3 and word not in _STOPWORDS and not word.isdigit()
        ),
    )


def is_stale_write(body: str) -> bool:
    """历史全文件写入是旧快照, 直接注入会让当前仓库回退到过期版本."""
    return body.lstrip().startswith(_STALE_WRITE_PREFIX)


def _is_low_density(body: str) -> bool:
    """长中文文本里重复填充占主导时视为低信息密度证据.

    英文散文的字符种类天然少, 只有 CJK 占比足够高时才启用该判断。
    """
    compact = "".join(body.split())
    if len(compact) < _LOW_DENSITY_LENGTH:
        return False
    if len(_CJK_CHAR_RE.findall(compact)) / len(compact) < _LOW_DENSITY_CJK_SHARE:
        return False
    return len(set(compact)) / len(compact) < _LOW_DENSITY_RATIO


def _matches_target_path(features: QueryFeatures, body: str) -> bool:
    """证据里的代码块路径直接命中查询中的文件时, 视为可立即采用的补丁."""
    if not features.paths:
        return False
    known = {path.lower() for path in features.paths}
    known.update(path.rsplit("/", 1)[-1] for path in known)
    for match in _FENCE_PATH_RE.finditer(body):
        fence_path = match.group(1).lower()
        if fence_path in known or fence_path.rsplit("/", 1)[-1] in known:
            return True
    return False


def _symbol_keyword_hits(features: QueryFeatures, symbols: str) -> int:
    """查询关键词命中证据符号片段的数量, 用于把函数级证据排到散文之上."""
    parts: set[str] = set()
    for symbol in symbols.split():
        parts.update(
            part for part in re.split(r"[^a-z0-9]+", symbol.lower()) if len(part) >= 3
        )
    if not parts:
        return 0
    return sum(1 for word in features.keywords if any(part.startswith(word) for part in parts))


def evidence_adjustment(
    features: QueryFeatures, body: str, paths: str = "", symbols: str = ""
) -> float:
    """对代码、短语、标识符匹配加分, 对陈旧写入和纯文档内容减分."""
    text = body.lower()
    meta = f"{text} {paths.lower()} {symbols.lower()}"
    adjustment = 0.0
    if is_stale_write(body):
        adjustment -= 0.05
    if body.lstrip().startswith(EPISODE_PREFIX):
        adjustment += 0.02
    if _ACTION_RE.search(body):
        adjustment += 0.005
    code_hits = len(_CODE_RE.findall(body))
    if code_hits:
        adjustment += min(0.015, 0.0015 * code_hits)
    elif not _USEFUL_RE.search(body):
        adjustment -= 0.02
    # 代码清单天然字符重复率高, 密度惩罚只用于没有代码的纯填充文本。
    if not code_hits and _is_low_density(body):
        adjustment -= 0.03
    if _FAILED_ATTEMPT_RE.search(body):
        adjustment -= 0.01
    if _matches_target_path(features, body):
        adjustment += 0.012
    if code_hits and _VALIDATED_RE.search(body):
        adjustment += 0.012
    adjustment += min(0.018, 0.006 * _symbol_keyword_hits(features, symbols))
    phrase_hits = sum(1 for phrase in features.phrases if phrase in text)
    cjk_hits = sum(1 for gram in features.cjk_grams if gram in body)
    adjustment += min(0.045, 0.015 * (phrase_hits + cjk_hits))
    identifier_hits = sum(1 for item in features.identifiers if item in meta)
    adjustment += min(0.012, 0.003 * identifier_hits)
    return adjustment
