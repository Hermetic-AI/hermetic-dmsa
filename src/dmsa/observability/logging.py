"""结构化 logger 门面（PD6）。

设计原则：
- 零依赖：只用 stdlib ``logging`` + ``json``。
- 关键操作（add_memory / backward / aggregate / apply）记录结构化字段。
- 不替代 stdlib logger：而是包装它，把 extra 字段序列化为 JSON 或彩色文本。

装配（级别 / 颜色 / 格式 / 文件）在 ``dmsa.observability.log_config``，本模块只负责
"拿 logger、打日志"。
"""

from __future__ import annotations

import logging
from typing import Any

from dmsa.observability.log_config import (
    LogLevel,
    add_logging_args,
    apply_cli_logging,
    configure_root,
    parse_format,
    parse_level,
    setup_logging,
    should_colorize,
)
from dmsa.observability.log_formatters import JsonFormatter, TextFormatter

# 默认 logger 名称
_DEFAULT_LOGGER_NAME = "dmsa"

# 包装器 -> 真实调用方的栈深度：_log -> info() -> 业务代码
_CALLER_STACKLEVEL = 3


class JsonLogger:
    """JSON logger 包装器。"""

    def __init__(self, name: str = _DEFAULT_LOGGER_NAME) -> None:
        """初始化 JSON logger 包装器, 绑定底层 logging logger。"""
        self._logger = logging.getLogger(name)
        self._name = name

    @property
    def name(self) -> str:
        """底层 stdlib logger 名称。"""
        return self._name

    def isEnabledFor(self, level: LogLevel) -> bool:
        """判断某级别是否会真的输出，用于跳过昂贵的日志构造。"""
        return self._logger.isEnabledFor(level)

    def _log(self, level: int, msg: str, **fields: Any) -> None:
        # stacklevel=3: 跳过 _log 与 info/debug/... 这层包装，让行号指向真实调用方。
        self._logger.log(level, msg, extra=fields, stacklevel=_CALLER_STACKLEVEL)

    def log(self, level: LogLevel, msg: str, **fields: Any) -> None:
        """按显式级别整数记录日志（供中间件按状态码选择级别）。"""
        self._log(int(level), msg, **fields)

    def trace(self, msg: str, **fields: Any) -> None:
        """记录 TRACE（5）级别日志，用于逐帧调试。"""
        self._log(5, msg, **fields)

    def debug(self, msg: str, **fields: Any) -> None:
        """记录 DEBUG 级别日志。"""
        self._log(logging.DEBUG, msg, **fields)

    def info(self, msg: str, **fields: Any) -> None:
        """记录 INFO 级别日志。"""
        self._log(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        """记录 WARNING 级别日志。"""
        self._log(logging.WARNING, msg, **fields)

    def error(self, msg: str, **fields: Any) -> None:
        """记录 ERROR 级别日志。"""
        self._log(logging.ERROR, msg, **fields)

    def exception(self, msg: str, **fields: Any) -> None:
        """记录 ERROR 级别日志，附带当前异常 traceback。"""
        self._logger.exception(msg, extra=fields, stacklevel=_CALLER_STACKLEVEL - 1)


def get_logger(name: str = _DEFAULT_LOGGER_NAME) -> JsonLogger:
    """获取 JSON logger 实例。"""
    return JsonLogger(name)


__all__ = [
    "JsonFormatter",
    "JsonLogger",
    "LogLevel",
    "TextFormatter",
    "add_logging_args",
    "apply_cli_logging",
    "configure_root",
    "get_logger",
    "parse_format",
    "parse_level",
    "setup_logging",
    "should_colorize",
]
