"""基于标准库 ast 的 Python 符号快照: 文件指纹、限定符号与失效判断.

快照只描述当前工作区的真实源码事实, 不推断调用关系, 也不修改任何文件.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from dmsa.memory.coding.code_anchors import CodeAnchor

# 这些目录不参与快照, 避免把依赖与构建产物当成仓库代码.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "site-packages",
    }
)
# 单次快照最多读取的 Python 文件数, 防止误扫超大目录。
MAX_FILES = 500
# 单文件最多记录的符号数, 防止超大文件拖慢扫描.
MAX_SYMBOLS_PER_FILE = 400


@dataclass(frozen=True)
class SymbolEntry:
    """一个限定符号及其位置."""

    name: str
    path: str
    line: int
    kind: str


@dataclass(frozen=True)
class RepoSnapshot:
    """仓库相对路径、文件指纹与限定符号的只读快照."""

    root: str
    files: tuple[tuple[str, str], ...] = ()
    symbols: tuple[SymbolEntry, ...] = ()

    @property
    def file_paths(self) -> frozenset[str]:
        """返回快照覆盖的仓库相对路径集合."""
        return frozenset(path for path, _ in self.files)

    @property
    def symbol_names(self) -> frozenset[str]:
        """返回快照内的限定符号集合."""
        return frozenset(item.name for item in self.symbols)

    def to_dict(self) -> dict[str, object]:
        """返回可持久化的规范字段."""
        return {
            "root": self.root,
            "files": [list(item) for item in self.files],
            "symbols": [
                {"name": item.name, "path": item.path, "line": item.line, "kind": item.kind}
                for item in self.symbols
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RepoSnapshot:
        """从持久化字段重建快照."""
        root = str(data.get("root", ""))
        raw_files = data.get("files", [])
        raw_symbols = data.get("symbols", [])
        files: list[tuple[str, str]] = []
        if isinstance(raw_files, list):
            for item in raw_files:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    files.append((str(item[0]), str(item[1])))
        symbols: list[SymbolEntry] = []
        if isinstance(raw_symbols, list):
            for item in raw_symbols:
                if not isinstance(item, dict):
                    continue
                symbols.append(
                    SymbolEntry(
                        name=str(item.get("name", "")),
                        path=str(item.get("path", "")),
                        line=int(item.get("line", 0)),
                        kind=str(item.get("kind", "")),
                    )
                )
        return cls(root=root, files=tuple(files), symbols=tuple(symbols))


class _SymbolVisitor(ast.NodeVisitor):
    """收集类与函数的限定符号及行号."""

    def __init__(self, path: str) -> None:
        """绑定文件相对路径."""
        self.path = path
        self.stack: list[str] = []
        self.entries: list[SymbolEntry] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """记录函数符号并继续遍历嵌套定义."""
        name = ".".join([*self.stack, node.name])
        if len(self.entries) < MAX_SYMBOLS_PER_FILE:
            self.entries.append(SymbolEntry(name, self.path, node.lineno, "function"))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """记录类符号并继续遍历嵌套定义."""
        name = ".".join([*self.stack, node.name])
        if len(self.entries) < MAX_SYMBOLS_PER_FILE:
            self.entries.append(SymbolEntry(name, self.path, node.lineno, "class"))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """记录函数符号."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """记录异步函数符号."""
        self._visit_function(node)


def _file_digest(data: bytes) -> str:
    """返回文件内容指纹."""
    return hashlib.sha256(data).hexdigest()[:16]


def _iter_python_files(root: Path) -> list[Path]:
    """按稳定顺序返回仓库内的 Python 文件, 跳过依赖与构建目录."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
        if len(found) >= MAX_FILES:
            break
    return found


def build_snapshot(root: str | Path, max_files: int = MAX_FILES) -> RepoSnapshot:
    """扫描工作区生成 Python 符号快照; 解析失败的文件只记录指纹."""
    base = Path(root).expanduser()
    if not base.is_dir():
        return RepoSnapshot(root=str(base))
    files: list[tuple[str, str]] = []
    symbols: list[SymbolEntry] = []
    for path in _iter_python_files(base):
        if len(files) >= max_files:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        relative = path.relative_to(base).as_posix()
        files.append((relative, _file_digest(data)))
        try:
            tree = ast.parse(data)
        except (SyntaxError, ValueError):
            continue
        visitor = _SymbolVisitor(relative)
        visitor.visit(tree)
        symbols.extend(visitor.entries)
    return RepoSnapshot(root=str(base), files=tuple(files), symbols=tuple(symbols))


def _looks_like_repo_path(path: str) -> bool:
    """判断路径是否像仓库内的源码路径, 避免误判外部引用."""
    return bool(path) and ("/" in path or path.endswith((".py", ".pyi")))


def stale_anchors(
    snapshot: RepoSnapshot, anchors: tuple[CodeAnchor, ...]
) -> tuple[CodeAnchor, ...]:
    """返回在当前快照中已不存在或已改名的锚点.

    判定保持保守: 只有路径确实消失, 或限定符号与末段名称都找不到时才判定失效.
    """
    if not snapshot.files:
        return ()
    paths = snapshot.file_paths
    names = snapshot.symbol_names
    last_names = {name.rsplit(".", 1)[-1] for name in names}
    stale: list[CodeAnchor] = []
    for anchor in anchors:
        if (
            anchor.path
            and _looks_like_repo_path(anchor.path)
            and anchor.path not in paths
            and anchor.kind in {"modifies", "mentions", "frame"}
        ):
            stale.append(anchor)
            continue
        if (
            anchor.symbol
            and anchor.kind == "defines"
            and anchor.symbol not in names
            and anchor.symbol.rsplit(".", 1)[-1] not in last_names
        ):
            stale.append(anchor)
    return tuple(stale)
