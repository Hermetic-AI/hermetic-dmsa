"""hermetic-dmsa — 生产级别的可微记忆层。

实现论文方向 1（"基于文本反向传播的 Agent 长短期记忆可微自进化"）的工程化产出。
5 层严格向下架构：

    L1 dmsa.memory       → pmd
    L2 dmsa.gradient     → L1, backends
    L3 dmsa.toolbp       → L1, L2
    L4 dmsa.mcp_server   → L1, L2, L3
    L5 integrations/*    → L4（通过 MCP 协议）

公共 API 入口：使用 `dmsa.MemoryLayer` 创建记忆层，通过 `dmsa.SGAAggregator` 聚合梯度，
通过 `dmsa.ToolBPEngine` 反向传播到工具 schema。详见 docs/api-reference.md。
"""

from __future__ import annotations

from dmsa.config import DMSAConfig, load_config
from dmsa.errors import (
    DMSAError,
    ErrorCode,
)

# 版本号（由 setuptools_scm 从 git tag 自动生成；fallback 在 _version.py）
try:
    from dmsa._version import __version__
except ImportError:  # pragma: no cover - 未安装时（源码布局）
    __version__ = "0.1.0.dev0"

# 公共 API 导出（具体实现在子模块完成后再补）
__all__ = [
    "DMSAConfig",
    "DMSAError",
    "ErrorCode",
    "__version__",
    "load_config",
]
