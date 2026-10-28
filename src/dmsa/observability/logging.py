"""结构化 JSON logger（PD6）。

设计原则：
- 零依赖：只用 stdlib ``logging`` + ``json``。
- 关键操作（add_memory / backward / aggregate / apply）记录结构化字段。
- 不替代 stdlib logger：而是包装它，把 extra 字段序列化为 JSON。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# 默认 logger 名称
_DEFAULT_LOGGER_NAME = "dmsa"


class JsonFormatter(logging.Formatter):
    """把 log record 序列化为 JSON 字符串。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化 log record 为 JSON 字符串。"""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 把 LogRecord 的所有非标准字段（extra）合并到 payload
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key in standard_attrs or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class JsonLogger:
    """JSON logger 包装器。"""

    def __init__(self, name: str = _DEFAULT_LOGGER_NAME) -> None:
        """初始化 JSON logger 包装器, 绑定底层 logging logger。"""
        self._logger = logging.getLogger(name)
        self._name = name

    def _log(self, level: int, msg: str, **fields: Any) -> None:
        self._logger.log(level, msg, extra=fields)

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
        self._logger.exception(msg, extra=fields)


def configure_root(
    level: int = logging.INFO,
    stream: Any = None,
) -> None:
    """配置根 logger：JSON formatter + 单一 stream handler。

    重复调用是幂等的：先清空已有 handler 再加新的。
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str = _DEFAULT_LOGGER_NAME) -> JsonLogger:
    """获取 JSON logger 实例。"""
    return JsonLogger(name)


__all__ = ["JsonFormatter", "JsonLogger", "configure_root", "get_logger"]
