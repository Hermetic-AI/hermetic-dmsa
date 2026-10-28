"""只读数据库访问helper：打开只读连接并把游标结果转成 dict。

依赖规则：只 import stdlib + dmsa.errors。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from dmsa.errors import MemoryLayerError

__all__ = ["open_readonly_db"]


def open_readonly_db(path: str) -> sqlite3.Connection:
    """打开 SQLite 数据库（只读模式）。

    Args:
        path: SQLite 文件路径。``:memory:`` 不可用（只读模式不支持）。

    Returns:
        :class:`sqlite3.Connection`。

    Raises:
        MemoryLayerError: 数据库无法打开或表结构不匹配。
    """
    if path == ":memory:":
        raise MemoryLayerError(
            "cannot inspect :memory: database (use a file path)",
            details={"hint": "use --storage ~/.hermetic-dmsa/db.sqlite"},
        )
    if not path or path.startswith("~"):
        path = _expand_user(path)
    try:
        # uri 格式必须用 file: 前缀
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise MemoryLayerError(
            f"failed to open database: {exc}",
            details={"path": path, "error": str(exc)},
        ) from exc
    return conn


def _expand_user(path: str) -> str:
    """展开 ``~`` 到用户主目录。"""
    return os.path.expanduser(path)


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """把游标当前结果集按列名转成 dict 列表（列数与行数严格一致）。"""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
