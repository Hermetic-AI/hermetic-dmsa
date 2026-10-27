"""CJK 与 ASCII 混合查询的 FTS 词项展开."""

from __future__ import annotations

import re
from itertools import pairwise

GRAM = 3
MAX_QUERY_TERMS = 64
_CJK_RANGES = "㐀-䶿一-鿿぀-ヿ가-힯"
_RUN_RE = re.compile(f"[{_CJK_RANGES}]+|[^\\s{_CJK_RANGES}]+")
_ASCII_RE = re.compile(r"[0-9A-Za-z_][0-9A-Za-z_.+/:-]*")
_IDENT_SEPARATOR_RE = re.compile(r"[._/:\-]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[0-9]+")
_CAMEL_DETECT_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2,}[a-z]")


def _is_cjk(run: str) -> bool:
    """判断词段是否由 CJK/假名/谚文组成."""
    return bool(run) and bool(re.fullmatch(f"[{_CJK_RANGES}]+", run))


def _grams(run: str, gram: int) -> tuple[str, ...]:
    """产出滑窗 n-gram; 短于 gram 时保留原串以避免静默丢词."""
    if len(run) < gram:
        return (run,)
    return tuple(run[index : index + gram] for index in range(len(run) - gram + 1))


def _identifier_parts(term: str) -> tuple[str, ...]:
    """拆分蛇形、点分、路径式和驼峰标识符, 让函数名片段也能命中 trigram 索引."""
    if not _IDENT_SEPARATOR_RE.search(term) and not _CAMEL_DETECT_RE.search(term):
        return ()
    parts: list[str] = []
    for chunk in _IDENT_SEPARATOR_RE.split(term):
        pieces = _CAMEL_RE.findall(chunk)
        for piece in pieces if "".join(pieces) == chunk else (chunk,):
            if len(piece) >= GRAM:
                parts.append(piece.lower())
    unique = tuple(dict.fromkeys(parts))
    pairs = tuple("_".join(pair) for pair in pairwise(unique))
    return tuple(dict.fromkeys((*unique, *pairs)))


def query_terms(text: str, gram: int = GRAM) -> tuple[str, ...]:
    """把自然语言查询展开成 trigram 虚表可用的词项序列.

    unicode61 会把整段无空格的中文切成单一 token, 导致中文查询零召回。FTS5
    trigram 分词按字符三元组索引, 因此中文需要展开成 n-gram 才能命中。短于 gram
    的词项没有对应三元组, 由 storage_queries.substring_candidates 的 LIKE 通道兜底。
    """
    terms: list[str] = []
    for token in text.split():
        for run in _RUN_RE.findall(token):
            if _is_cjk(run):
                terms.extend(_grams(run, gram))
            else:
                for term in _ASCII_RE.findall(run):
                    terms.append(term)
                    terms.extend(_identifier_parts(term))
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
        if len(unique) >= MAX_QUERY_TERMS:
            break
    return tuple(unique)
