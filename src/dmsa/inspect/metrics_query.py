"""只读查询：backward_gradients 表的统计与最近记录。

依赖规则：只 import stdlib + 同层 inspect.db_access。
"""

from __future__ import annotations

from typing import Any

from dmsa.inspect.db_access import _rows_as_dicts, open_readonly_db

__all__ = ["inspect_metrics"]


def inspect_metrics(path: str, *, since: float | None = None) -> dict[str, Any]:
    """导出 backward_gradients 表的统计 + 最近记录。

    Args:
        path: SQLite 路径。
        since: Unix 时间戳；只统计该时间之后的记录（None = 全部）。

    Returns:
        dict 含：
        - total_gradients: 总数
        - recent_gradients: 最近 20 条
        - by_source: {FeedbackSource: count}
        - token_cost_total: 总 token 消耗
    """
    conn = open_readonly_db(path)
    try:
        cursor = conn.cursor()
        # 检查表是否存在
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backward_gradients'"
        )
        if cursor.fetchone() is None:
            return {
                "schema": "dmsa.inspect.metrics.v1",
                "total_gradients": 0,
                "recent_gradients": [],
                "by_source": {},
                "token_cost_total": 0,
                "message": "backward_gradients table not found (no backward pass yet)",
            }
        where = "WHERE created_at >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        cursor.execute(f"SELECT COUNT(*) FROM backward_gradients {where}", params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT * FROM backward_gradients {where} ORDER BY created_at DESC LIMIT 20",
            params,
        )
        recent = _rows_as_dicts(cursor)
        cursor.execute(
            "SELECT feedback_source, COUNT(*) FROM backward_gradients "
            f"{where} GROUP BY feedback_source",
            params,
        )
        by_source = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            f"SELECT COALESCE(SUM(token_cost), 0) FROM backward_gradients {where}",
            params,
        )
        token_total = cursor.fetchone()[0] or 0
        return {
            "schema": "dmsa.inspect.metrics.v1",
            "since": since,
            "total_gradients": total,
            "recent_gradients": recent,
            "by_source": by_source,
            "token_cost_total": token_total,
        }
    finally:
        conn.close()
