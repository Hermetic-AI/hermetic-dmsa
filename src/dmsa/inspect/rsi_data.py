# ruff: noqa: D415, RUF002
"""只读展示统一 DMSA 的 RSI 运行轨迹与策略版本。"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from dmsa.inspect.memory_browser_data import _connect_readonly


def read_rsi(path: Path) -> dict[str, Any]:
    """返回有限审计事件，不返回查询正文或模型凭据。"""
    with closing(_connect_readonly(path)) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rsi_events'"
        ).fetchone()
        if not exists:
            return {"events": [], "active": [], "traces": 0, "feedback": 0}
        events = [
            dict(row) for row in db.execute("SELECT * FROM rsi_events ORDER BY id DESC LIMIT 100")
        ]
        for event in events:
            event["payload"] = json.loads(event["payload"])
        active = [
            dict(row)
            for row in db.execute(
                "SELECT a.scope,a.version,v.policy FROM rsi_active a JOIN rsi_versions v "
                "ON a.scope=v.scope AND a.version=v.version"
            )
        ]
        return {
            "events": events,
            "active": active,
            "versions": [
                dict(row)
                for row in db.execute(
                    "SELECT scope,version,policy FROM rsi_versions ORDER BY rowid DESC LIMIT 100"
                )
            ],
            "traces": db.execute("SELECT count(*) FROM rsi_traces").fetchone()[0],
            "feedback": db.execute("SELECT count(*) FROM rsi_feedback").fetchone()[0],
        }
