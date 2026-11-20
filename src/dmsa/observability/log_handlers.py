"""日志 handler。

单独成模块, 让装配逻辑(``log_config``)只依赖 handler 的身份, 不必同时持有
基类解析与 handler 定义。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    # logging.StreamHandler 直到 3.11 才在运行时支持下标, 3.10 上写
    # ``logging.StreamHandler[Any]`` 会在导入期抛 TypeError。基类表达式是运行时求值的,
    # ``from __future__ import annotations`` 帮不上忙, 因此类型检查走参数化版本,
    # 运行期走未参数化的原始类。
    _StreamHandlerBase = logging.StreamHandler[IO[str]]
else:
    _StreamHandlerBase = logging.StreamHandler


class ManagedStreamHandler(_StreamHandlerBase):
    """由本模块安装的流 handler，用于重复装配时只摘掉自己的 handler。"""


class ManagedRotatingFileHandler(RotatingFileHandler):
    """由本模块安装的滚动文件 handler。"""


__all__ = ["ManagedRotatingFileHandler", "ManagedStreamHandler"]
