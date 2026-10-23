# ruff: noqa: RUF002, D415
"""Qoder Stop hook: 把每一轮编码过程增量沉淀进 dmsa CodingMemory。

用法见同目录 ``README.md``。设计约束：

* L5 集成层不 import ``dmsa``，写库通过 MCP stdio 子进程（见 dmsa_mcp_client）。
* 按 transcript 行偏移增量，重复触发不会产生重复记忆。
* hook 永不阻塞对话：任何异常只记日志，退出码 0。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dmsa_mcp_client
from distill import Turn, build_memory_text, parse_turn

STATE_FILENAME = "state.json"
LOGGER_NAME = "qoder.dmsa.hook"


def home_dir() -> Path:
    """记忆与状态所在的用户目录。"""
    return Path(os.environ.get("DMSA_CODING_MEMORY_HOME", str(Path.home() / ".dmsa")))


def _configure_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_path.parent / "hook.log", maxBytes=512_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _load_payload(logger: logging.Logger) -> dict[str, Any]:
    """读取 stdin 的 hook payload, 区分空输入、JSON 截断和非对象三种情况。"""
    raw = sys.stdin.read()
    if not raw.strip():
        logger.info("stdin empty (cwd=%s)", os.getcwd())
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("stdin is not valid JSON: %s raw_len=%d head=%s", exc, len(raw), raw[:120])
        return {}
    if not isinstance(parsed, dict):
        logger.warning("stdin JSON is not an object: %s", type(parsed).__name__)
        return {}
    return parsed


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _scope_from(payload: dict[str, Any], turn: Turn) -> str:
    override = os.environ.get("DMSA_CODING_MEMORY_SCOPE")
    if override:
        return override
    cwd = payload.get("cwd") or turn.cwd
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).name or "default"
    return "default"


def _session_key(payload: dict[str, Any], turn: Turn) -> str:
    return str(payload.get("session_id") or turn.session_id or "unknown")


def _resolve_transcript(payload: dict[str, Any], logger: logging.Logger) -> str | None:
    """取 payload 的 transcript 路径, 缺失时按 session_id 在 Qoder 项目目录反查。"""
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and Path(transcript).is_file():
        return transcript
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[\w-]+", session_id):
        return None
    root = Path(os.environ.get("DMSA_QODER_PROJECTS", str(Path.home() / ".qoder-cn" / "projects")))
    try:
        for candidate in root.glob(f"*/{session_id}.jsonl"):
            if candidate.is_file():
                logger.info("fallback transcript resolved from session=%s", session_id[:8])
                return str(candidate)
    except OSError:
        return None
    return None


def run() -> int:
    """执行一次 hook：抽取增量 → 脱敏 → MCP 写入 → 前移游标。"""
    base = home_dir()
    logger = _configure_logger(base / "hook.log")
    if os.environ.get("DMSA_HOOK_DISABLE") == "1":
        logger.info("hook disabled by DMSA_HOOK_DISABLE")
        return 0
    payload = _load_payload(logger)
    transcript = _resolve_transcript(payload, logger)
    if transcript is None:
        logger.info("skip: no transcript, payload keys=%s", sorted(payload))
        return 0
    state_path = base / STATE_FILENAME
    state = _load_state(state_path)
    key = _session_key(payload, Turn("", "", "", 0, 0))
    cursor = state.get(key)
    start_line = cursor.get("last_line", 0) if isinstance(cursor, dict) else 0

    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("skip: cannot read transcript %s (%s)", transcript, exc)
        return 0

    turn = parse_turn(lines, min(start_line, len(lines)), key)
    if turn.is_empty:
        state[key] = {"last_line": len(lines)}
        _save_state(state_path, state)
        logger.info("session=%s no new turn content at L%d..%d", key[:8], start_line, len(lines))
        return 0

    scope = _scope_from(payload, turn)
    content = build_memory_text(turn, scope)
    if os.environ.get("DMSA_HOOK_DRY_RUN") == "1":
        logger.info("dry-run would record %d chars:\n%s", len(content), content)
        print(content)
        return 0

    try:
        result = dmsa_mcp_client.record(
            scope=scope,
            request_id=turn.request_id(scope),
            session_id=key,
            content=content,
        )
    except Exception as exc:
        logger.error("record failed session=%s error=%s", key[:8], exc)
        return 0

    state[key] = {"last_line": turn.end_line, "last_request_id": result.get("request_id")}
    _save_state(state_path, state)
    logger.info(
        "recorded session=%s L%d..%d revision=%s chars=%d",
        key[:8],
        turn.start_line,
        turn.end_line,
        result.get("revision"),
        len(content),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        logging.getLogger(LOGGER_NAME).exception("hook crashed: %s", exc)
        sys.exit(0)
