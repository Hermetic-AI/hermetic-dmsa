"""日志装配：环境变量驱动的根 logger 配置。

可配置项（全部有默认值，不设也能直接跑）：

- ``DMSA_LOG_LEVEL``：DEBUG / INFO / WARNING / ERROR / CRITICAL / TRACE，默认 ``INFO``。
- ``DMSA_LOG_FORMAT``：``text``（彩色单行，默认）或 ``json``（结构化）。
- ``DMSA_LOG_COLOR``：``auto`` / ``always`` / ``never``，默认 ``auto``（仅 TTY 上色）。
- ``DMSA_LOG_FILE``：额外的滚动日志文件路径（``DMSA_LOG_FILE_MAX_BYTES`` /
  ``DMSA_LOG_FILE_BACKUP_COUNT`` 控制轮转）。

设计约束：控制台 handler 默认写 **stderr**，因为 stdio MCP server 把 stdout 留给
JSON-RPC 协议本身，日志串进去会破坏协议帧。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any, Literal

from dmsa.errors import InvalidConfigValueError
from dmsa.observability.log_formatters import TRACE_LOG_LEVEL, JsonFormatter, TextFormatter

LogLevel = int
LogFormat = Literal["text", "json"]

# 三方库在 INFO 级别过于吵闹，默认压到 WARNING；调用方显式要 DEBUG 时才放开。
_NOISY_LOGGERS: tuple[str, ...] = (
    "aiologic",
    "anyio",
    "asyncio",
    "httpcore",
    "httpx",
    "h11",
    "openai",
    "qdrant_client",
    "urllib3",
    "watchfiles",
    "websockets",
)

_LEVEL_NAMES: dict[str, int] = {
    "TRACE": TRACE_LOG_LEVEL,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "OFF": logging.CRITICAL + 10,
}

DEFAULT_FILE_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_FILE_BACKUP_COUNT = 3


class ManagedStreamHandler(logging.StreamHandler[Any]):
    """由本模块安装的流 handler，用于重复装配时只摘掉自己的 handler。"""


class ManagedRotatingFileHandler(RotatingFileHandler):
    """由本模块安装的滚动文件 handler。"""


def parse_level(raw: str | int | None, default: LogLevel = logging.INFO) -> LogLevel:
    """把 ``"info"`` / ``20`` / ``None`` 统一解析成 logging 级别整数。"""
    if raw is None:
        return default
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().upper()
    if not text:
        return default
    if text in _LEVEL_NAMES:
        return _LEVEL_NAMES[text]
    try:
        return int(text)
    except ValueError as exc:
        raise InvalidConfigValueError(
            f"unknown log level {raw!r}; expected one of {sorted(_LEVEL_NAMES)}"
        ) from exc


def parse_format(raw: str | None, default: LogFormat = "text") -> LogFormat:
    """解析日志输出格式，只接受 ``text`` 与 ``json``。"""
    text = (raw or "").strip().lower()
    if not text:
        return default
    if text in ("text", "console", "pretty"):
        return "text"
    if text in ("json", "structured"):
        return "json"
    raise InvalidConfigValueError(f"unknown log format {raw!r}; expected text or json")


def build_formatter(fmt: LogFormat, *, use_colors: bool) -> logging.Formatter:
    """按格式构造 formatter。"""
    if fmt == "json":
        return JsonFormatter()
    return TextFormatter(use_colors=use_colors)


def should_colorize(stream: IO[str] | None, mode: str | None = None) -> bool:
    """判断是否给该输出流上 ANSI 颜色。

    优先级：显式 ``mode`` > ``NO_COLOR`` > ``FORCE_COLOR`` / ``CLICOLOR_FORCE`` > TTY 探测。
    """
    resolved = (mode or os.environ.get("DMSA_LOG_COLOR") or "auto").strip().lower()
    if resolved in ("always", "true", "1", "yes", "on"):
        return True
    if resolved in ("never", "false", "0", "no", "off"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    target = stream if stream is not None else sys.stderr
    try:
        return bool(target.isatty())
    except Exception:  # pragma: no cover - 关闭的流或重定向实现异常
        return False


def _env_int(name: str, default: int) -> int:
    """读取整数型环境变量，非法值回落到默认。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _ensure_utf8(stream: Any) -> Any:
    """把非 UTF-8 的输出流就地改成 UTF-8，避免中文日志在 Windows 控制台乱码。"""
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if not hasattr(stream, "reconfigure") or encoding.startswith("utf8"):
        return stream
    try:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (OSError, ValueError):  # pragma: no cover - 特殊流（如捕获缓冲）不支持重配置
        return stream
    return stream


def configure_root(
    level: LogLevel | str | None = None,
    stream: Any = None,
    *,
    fmt: LogFormat | str | None = None,
    color: str | None = None,
    log_file: str | os.PathLike[str] | None = None,
    force: bool = True,
) -> LogLevel:
    """配置根 logger，返回实际生效的级别。

    Args:
        level: 级别名或整数；``None`` 时读 ``DMSA_LOG_LEVEL``（默认 INFO）。
        stream: 控制台输出流；``None`` 时用 stderr 并读 ``DMSA_LOG_STREAM``。
        fmt: ``text`` 或 ``json``；``None`` 时读 ``DMSA_LOG_FORMAT``。
        color: ``auto`` / ``always`` / ``never``；``None`` 时读 ``DMSA_LOG_COLOR``。
        log_file: 额外滚动文件路径；``None`` 时读 ``DMSA_LOG_FILE``。
        force: 是否先清空根 logger 上已有的 handler（默认 True，保证幂等）。

    Side effects: 修改根 logger 与三方库 logger 的级别 / handler。
    """
    resolved_level = parse_level(level if level is not None else os.environ.get("DMSA_LOG_LEVEL"))
    resolved_format = parse_format(fmt if fmt is not None else os.environ.get("DMSA_LOG_FORMAT"))
    target = _ensure_utf8(stream if stream is not None else sys.stderr)
    root = logging.getLogger()
    root.setLevel(resolved_level)
    if force:
        # 只摘掉自己装过的 handler，避免破坏测试框架或宿主应用已有的日志配置。
        for handler in list(root.handlers):
            if isinstance(handler, (ManagedStreamHandler, ManagedRotatingFileHandler)):
                root.removeHandler(handler)
                handler.close()

    console = ManagedStreamHandler(target)
    console.setLevel(resolved_level)
    console.setFormatter(
        build_formatter(resolved_format, use_colors=should_colorize(target, color))
    )
    root.addHandler(console)

    path = log_file if log_file is not None else os.environ.get("DMSA_LOG_FILE")
    if path and str(path).strip():
        _attach_file_handler(
            root,
            Path(str(path)).expanduser(),
            resolved_level,
            resolved_format,
            console,
        )
    quiet_third_party(resolved_level)
    return resolved_level


def _attach_file_handler(
    root: logging.Logger,
    path: Path,
    level: LogLevel,
    fmt: LogFormat,
    console: logging.Handler,
) -> None:
    """挂载滚动文件 handler；路径不可写时只在控制台告警一次。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = ManagedRotatingFileHandler(
            path,
            maxBytes=_env_int("DMSA_LOG_FILE_MAX_BYTES", DEFAULT_FILE_MAX_BYTES),
            backupCount=_env_int("DMSA_LOG_FILE_BACKUP_COUNT", DEFAULT_FILE_BACKUP_COUNT),
            encoding="utf-8",
        )
    except OSError as exc:
        console.setLevel(logging.NOTSET)
        logging.getLogger("dmsa.observability.log_config").warning(
            "log file unavailable; console logging only",
            extra={"log_file": str(path), "reason": str(exc)},
        )
        return
    file_handler.setLevel(level)
    file_handler.setFormatter(build_formatter(fmt, use_colors=False))
    root.addHandler(file_handler)


def setup_logging(
    level: LogLevel | str | None = None,
    stream: Any = None,
    *,
    fmt: str | None = None,
    log_file: str | None = None,
) -> LogLevel:
    """服务与 CLI 入口的统一装配入口（未显式传参时全部走环境变量）。"""
    return configure_root(level, stream, fmt=fmt, log_file=log_file)


def quiet_third_party(level: LogLevel = logging.INFO) -> None:
    """把吵闹的三方 logger 压到 WARNING；调用方显式要 DEBUG 时交回根级别控制。"""
    if level <= logging.DEBUG:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)
        return
    noisy_level = logging.WARNING if level <= logging.INFO else level
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy_level)


def add_logging_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """给 CLI 加上 ``--log-level`` / ``--log-format`` / ``--log-file`` 三个通用参数。"""
    parser.add_argument(
        "--log-level",
        default=None,
        help="日志级别：TRACE/DEBUG/INFO/WARNING/ERROR（默认取 DMSA_LOG_LEVEL 或 INFO）",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=None,
        help="日志格式：text 彩色单行（默认）或 json 结构化",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="额外写入的滚动日志文件路径（默认取 DMSA_LOG_FILE）",
    )
    return parser


def apply_cli_logging(args: argparse.Namespace) -> LogLevel:
    """按 CLI 参数装配日志，返回生效级别。"""
    return configure_root(
        level=getattr(args, "log_level", None),
        fmt=getattr(args, "log_format", None),
        log_file=getattr(args, "log_file", None),
    )


__all__ = [
    "JsonFormatter",
    "LogFormat",
    "LogLevel",
    "TextFormatter",
    "add_logging_args",
    "apply_cli_logging",
    "build_formatter",
    "configure_root",
    "parse_format",
    "parse_level",
    "quiet_third_party",
    "setup_logging",
    "should_colorize",
]
