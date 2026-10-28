"""代码骨架摘要: 从代码块提取签名、类成员与 import.

骨架只描述代码块里真实存在的结构, 不补全缺失实现, 也不做跨文件推断.
"""

from __future__ import annotations

import ast
import re

# 代码块头允许的语言标记; 空语言按 Python 尝试解析。
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+.-]*)[ \t]*\r?\n(.*?)```", re.S)
# 单个代码块最多提取的骨架行数, 避免超长文件拖慢扫描。
MAX_LINES_PER_BLOCK = 40


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """返回函数签名文本."""
    args = [item.arg for item in node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"{node.name}({', '.join(args)})"


def _python_skeleton(body: str) -> list[str]:
    """解析 Python 代码块, 返回签名与成员摘要."""
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        return []
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = ", ".join(alias.name for alias in node.names)
            lines.append(f"import {names}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "."
            names = ", ".join(alias.name for alias in node.names)
            lines.append(f"from {module} import {names}")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"class {node.name}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append(f"  def {_signature(item)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(f"def {_signature(node)}")
        if len(lines) >= MAX_LINES_PER_BLOCK:
            break
    return lines


def extract_skeleton(text: str, depth: int = 6) -> str:
    """返回代码块骨架摘要; 深度为 0 或没有可解析代码时返回空字符串."""
    if depth <= 0 or not isinstance(text, str) or not text:
        return ""
    lines: list[str] = []
    for language, body in _FENCE_RE.findall(text):
        if language and language.lower() not in {"python", "py", ""}:
            continue
        lines.extend(_python_skeleton(body))
        if len(lines) >= depth:
            break
    return "\n".join(lines[:depth])
