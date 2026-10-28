"""从轨迹文本确定性抽取代码锚点: 改动路径、定义符号、测试用例与堆栈帧.

锚点只描述原文里明确出现的信息, 不做语义推断, 也不生成新的修复方案.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 锚点种类: 改动文件 / 提及文件 / 定义符号 / 测试用例 / 堆栈帧.
KIND_MODIFIES = "modifies"
KIND_MENTIONS = "mentions"
KIND_DEFINES = "defines"
KIND_TEST = "test"
KIND_FRAME = "frame"

# 各类证据的置信度: 显式 diff 头与堆栈帧最可信, 纯提及最弱.
CONFIDENCE = {
    KIND_MODIFIES: 0.9,
    KIND_DEFINES: 0.9,
    KIND_TEST: 0.85,
    KIND_FRAME: 0.9,
    KIND_MENTIONS: 0.6,
}

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)", re.M)
_DIFF_NEW_RE = re.compile(r"^\+\+\+ b/(?P<path>\S+)", re.M)
_DIFF_OLD_RE = re.compile(r"^--- a/(?P<path>\S+)", re.M)
_FENCE_PATH_RE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]+path=([^\s`]+)")
_CLASS_RE = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>[A-Za-z_]\w*)")
_DEF_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)")
_TEST_ID_RE = re.compile(r"(?P<path>[\w./-]+\.py)::(?P<name>[\w.\-]+)")
_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line \d+, in (?P<name>[\w<>]+)')
_HUNK_RE = re.compile(r"^@@ ", re.M)
_PATH_LIKE_RE = re.compile(r"^[\w./-]+\.(?:py|pyi|ts|tsx|js|jsx|java|go|rs|rb|cs|cpp|cc|h|hpp)$")


@dataclass(frozen=True)
class CodeAnchor:
    """一条有原文依据的代码锚点."""

    path: str
    symbol: str
    kind: str
    confidence: float

    @property
    def key(self) -> tuple[str, str, str]:
        """返回用于去重的三元键."""
        return (self.path, self.symbol, self.kind)


def _normalize_path(path: str) -> str:
    """去掉 a/ b/ 前缀与多余空白, 统一使用仓库相对路径."""
    value = path.strip().strip("'\"")
    for prefix in ("b/", "a/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.replace("\\", "/").strip()


def _code_view(text: str) -> str:
    """还原 diff 中的真实代码行: 只保留新增与上下文行, 去掉补丁前缀."""
    if "diff --git" not in text and not _HUNK_RE.search(text):
        return text
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            lines.append(line[1:])
        else:
            lines.append(line)
    return "\n".join(lines)


def _class_qualified_symbols(text: str) -> list[tuple[str, str]]:
    """返回按缩进归属的 (限定符号, 原始符号) 列表, 覆盖类与嵌套函数."""
    results: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    for line in _code_view(text).splitlines():
        class_match = _CLASS_RE.match(line)
        if class_match:
            indent = len(class_match.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            name = class_match.group("name")
            stack.append((indent, name))
            results.append((name, name))
            continue
        def_match = _DEF_RE.match(line)
        if not def_match:
            continue
        indent = len(def_match.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        name = def_match.group("name")
        qualified = ".".join([*(item[1] for item in stack), name])
        results.append((qualified, name))
    return results


def _symbol_variants(qualified: str) -> tuple[str, ...]:
    """产生限定符号的逐级缩写形式, 便于不同书写方式都能精确命中."""
    parts = qualified.split(".")
    variants = [".".join(parts[index:]) for index in range(len(parts))]
    return tuple(dict.fromkeys(variants))


def _dedupe(anchors: list[CodeAnchor]) -> tuple[CodeAnchor, ...]:
    """按 (路径, 符号, 种类) 去重, 保留首次出现的顺序."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[CodeAnchor] = []
    for anchor in anchors:
        if anchor.key in seen:
            continue
        seen.add(anchor.key)
        unique.append(anchor)
    return tuple(unique)


def extract_anchors(text: str) -> tuple[CodeAnchor, ...]:
    """从任意轨迹文本抽取确定性代码锚点."""
    if not isinstance(text, str) or not text:
        return ()
    anchors: list[CodeAnchor] = []
    for match in _DIFF_GIT_RE.finditer(text):
        anchors.append(
            CodeAnchor(
                _normalize_path(match.group("new")),
                "",
                KIND_MODIFIES,
                CONFIDENCE[KIND_MODIFIES],
            )
        )
    for match in _DIFF_NEW_RE.finditer(text):
        anchors.append(
            CodeAnchor(
                _normalize_path(match.group("path")),
                "",
                KIND_MODIFIES,
                CONFIDENCE[KIND_MODIFIES],
            )
        )
    for match in _DIFF_OLD_RE.finditer(text):
        anchors.append(
            CodeAnchor(
                _normalize_path(match.group("path")),
                "",
                KIND_MENTIONS,
                CONFIDENCE[KIND_MENTIONS],
            )
        )
    for match in _FENCE_PATH_RE.finditer(text):
        anchors.append(
            CodeAnchor(
                _normalize_path(match.group(1)),
                "",
                KIND_MENTIONS,
                CONFIDENCE[KIND_MENTIONS],
            )
        )
    for qualified, name in _class_qualified_symbols(text):
        for variant in _symbol_variants(qualified):
            anchors.append(CodeAnchor("", variant, KIND_DEFINES, CONFIDENCE[KIND_DEFINES]))
        if name != qualified:
            anchors.append(CodeAnchor("", name, KIND_DEFINES, CONFIDENCE[KIND_DEFINES]))
    for match in _TEST_ID_RE.finditer(text):
        path = _normalize_path(match.group("path"))
        anchors.append(
            CodeAnchor(path, f"{path}::{match.group('name')}", KIND_TEST, CONFIDENCE[KIND_TEST])
        )
    for match in _FRAME_RE.finditer(text):
        anchors.append(
            CodeAnchor(
                _normalize_path(match.group("path")),
                match.group("name"),
                KIND_FRAME,
                CONFIDENCE[KIND_FRAME],
            )
        )
    return _dedupe(anchors)


def anchor_terms(anchors: tuple[CodeAnchor, ...]) -> tuple[str, ...]:
    """产生锚点可被精确匹配的小写检索词."""
    terms: list[str] = []
    for anchor in anchors:
        if anchor.path:
            terms.append(anchor.path.lower())
            terms.append(anchor.path.rsplit("/", 1)[-1].lower())
        if anchor.symbol:
            terms.append(anchor.symbol.lower())
            terms.append(anchor.symbol.rsplit(".", 1)[-1].lower())
            if "::" in anchor.symbol:
                terms.append(anchor.symbol.rsplit("::", 1)[-1].lower())
    return tuple(dict.fromkeys(term for term in terms if len(term) > 2))


def anchor_summary(anchors: tuple[CodeAnchor, ...], limit: int = 6) -> str:
    """把锚点压缩成一行可读摘要, 用于证据包中的结构化定位信息."""
    if not anchors:
        return ""
    parts: list[str] = []
    for anchor in anchors:
        if anchor.path and anchor.symbol:
            parts.append(f"{anchor.path}::{anchor.symbol}")
        elif anchor.path:
            parts.append(anchor.path)
        else:
            parts.append(anchor.symbol)
    unique = list(dict.fromkeys(parts))
    summary = "; ".join(unique[:limit])
    if len(unique) > limit:
        summary += f"; +{len(unique) - limit}"
    return summary
