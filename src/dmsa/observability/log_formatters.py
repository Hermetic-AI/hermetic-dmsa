"""日志 formatter：彩色文本（人读）与 JSON（机器读）两套。

设计原则：
- 零依赖：只用 stdlib ``logging`` + ``json`` + ANSI 转义序列。
- 文本行包含时间戳、级别、logger 名、``函数名:行号``、消息与 extra 字段。
- 两套 formatter 都带 ``location`` 与 ``trace_id``，方便按请求串联日志。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from dmsa.observability.trace import current_trace_id

# stdlib LogRecord 自带字段；其余都视为调用方通过 extra 传入的结构化字段
STANDARD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

TRACE_LOG_LEVEL = 5

# 三方库通过 extra 传入的内部字段，对人读日志没有价值
HIDDEN_FIELDS: frozenset[str] = frozenset({"color_message", "color_function"})


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """取出 LogRecord 上由 ``extra=`` 传入的自定义字段。"""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in STANDARD_ATTRS and key not in HIDDEN_FIELDS and not key.startswith("_")
    }


def record_location(record: logging.LogRecord) -> str:
    """拼出 ``模块.函数名:行号``，用于定位日志来源。"""
    return f"{record.name}:{record.funcName}:{record.lineno}"


def _ansi(code: str) -> str:
    return f"\033[{code}m"


_RESET = _ansi("0")
_DIM = _ansi("2")
_BOLD = _ansi("1")

# 级别 -> 名称样式。未列出的级别沿用 INFO 的配色。
_LEVEL_STYLE: dict[int, str] = {
    TRACE_LOG_LEVEL: _ansi("38;5;213"),
    logging.DEBUG: _ansi("36"),
    logging.INFO: _ansi("32"),
    logging.WARNING: _ansi("33"),
    logging.ERROR: _ansi("31"),
    logging.CRITICAL: _BOLD + _ansi("91"),
}


def _paint(text: str, style: str) -> str:
    """给文本套上 ANSI 样式；空样式原样返回。"""
    if not style:
        return text
    return f"{style}{text}{_RESET}"


class TextFormatter(logging.Formatter):
    """面向人眼与控制台的单行文本 formatter。"""

    def __init__(
        self,
        *,
        use_colors: bool = True,
        show_extra: bool = True,
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> None:
        """初始化文本 formatter。

        Args:
            use_colors: 是否输出 ANSI 颜色（非 TTY 时应关闭）。
            show_extra: 是否把 ``extra=`` 字段以 ``key=value`` 追加到行尾。
            datefmt: 时间戳日期部分格式，毫秒始终追加。
        """
        super().__init__(datefmt=datefmt)
        self.use_colors = use_colors
        self.show_extra = show_extra

    def format(self, record: logging.LogRecord) -> str:
        """渲染成 ``时间 | 级别 | logger:函数:行号 | trace | 消息 | 字段``。"""
        stamp = f"{self.formatTime(record, self.datefmt)}.{int(record.msecs):03d}"
        level = f"{record.levelname:<8}"
        location = record_location(record)
        message = record.getMessage()
        if self.show_extra:
            fields = self._format_fields(record)
            if fields:
                message = f"{message} {fields}"
        trace_id = current_trace_id()
        parts = [stamp, level, location]
        if trace_id:
            parts.append(f"trace={trace_id}")
        parts.append(message)

        if self.use_colors:
            level_style = _LEVEL_STYLE.get(record.levelno, _LEVEL_STYLE[logging.INFO])
            parts[0] = _paint(parts[0], _DIM)
            parts[1] = _paint(parts[1], level_style)
            parts[2] = _paint(parts[2], _ansi("38;5;33"))
            parts[-1] = _paint(parts[-1], _ansi("31") if record.levelno >= logging.ERROR else "")
            if trace_id:
                parts[3] = _paint(parts[3], _ansi("35"))
            separator = _paint(" | ", _DIM)
            rendered = separator.join(parts)
        else:
            rendered = " | ".join(parts)
        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            rendered = f"{rendered}\n{self.formatStack(record.stack_info)}"
        return rendered

    def _format_fields(self, record: logging.LogRecord) -> str:
        """把 extra 字段渲染成 ``key=value``，值带空格时加引号。"""
        pieces: list[str] = []
        for key, value in extra_fields(record).items():
            rendered = value if isinstance(value, str) else _safe_repr(value)
            if " " in rendered or not rendered:
                rendered = f'"{rendered}"'
            pieces.append(f"{key}={rendered}")
        return " ".join(pieces)


def _safe_repr(value: Any) -> str:
    """尽力把任意值转成单行字符串。"""
    try:
        text = str(value)
    except Exception:  # pragma: no cover - 取决于被记录对象的 __str__
        text = repr(value)
    return text.replace("\n", "\\n")


class JsonFormatter(logging.Formatter):
    """把 log record 序列化为 JSON 字符串。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化 log record 为 JSON 字符串。"""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "location": record_location(record),
            "line": record.lineno,
            "func": record.funcName,
            "message": record.getMessage(),
        }
        trace_id = current_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        for key, value in extra_fields(record).items():
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "STANDARD_ATTRS",
    "TRACE_LOG_LEVEL",
    "JsonFormatter",
    "TextFormatter",
    "extra_fields",
    "record_location",
]
