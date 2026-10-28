"""仓库符号快照的持久化读写.

快照只描述某个仓库根下存在哪些符号, 用来标记检索到的证据是否已经失效;
它与原文分开存放, 覆盖同一仓库根时只保留最新一份.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from dmsa.memory.coding.snapshot import RepoSnapshot


def save_snapshot(db: sqlite3.Connection, user_id: str, snapshot: RepoSnapshot) -> None:
    """保存 scope 的仓库符号快照, 同一 root 覆盖旧版本."""
    db.execute(
        "INSERT INTO repo_snapshots(user_id, root, created_at, payload) "
        "VALUES(?,?,?,?) ON CONFLICT(user_id, root) DO UPDATE SET "
        "created_at=excluded.created_at, payload=excluded.payload",
        (
            user_id,
            snapshot.root,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(snapshot.to_dict(), ensure_ascii=False),
        ),
    )


def load_snapshot(db: sqlite3.Connection, user_id: str) -> RepoSnapshot | None:
    """读取 scope 最近一次仓库快照; 没有快照或内容损坏时返回 None."""
    row = db.execute(
        "SELECT payload FROM repo_snapshots WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return RepoSnapshot.from_dict(json.loads(str(row["payload"])))
    except (TypeError, ValueError):
        return None
