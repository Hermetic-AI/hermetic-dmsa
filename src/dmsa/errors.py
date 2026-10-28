"""错误体系（PD4）。

所有 hermetic-dmsa 抛出的异常都继承自 :class:`DMSAError`，并带：

- 错误码（`DMSA-NNNN` 格式），便于日志过滤与可观测性
- 可序列化的 details dict（JSON 友好）
- 文档化的"何时 raise"约定

调用方可以 `except DMSAError` 捕获所有库内错误；按 code 细分处理时可
用 `except MemoryLayerError` / `except GradientAggregationError` 等。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """错误码枚举。格式 `DMSA-NNNN`，NNNN 按层 + 子类编号。"""

    # 基类 / 通用
    DMSA_0000 = "DMSA-0000"  # DMSAError 基类
    DMSA_0001 = "DMSA-0001"  # Configuration error
    DMSA_0002 = "DMSA-0002"  # Serialization error

    # L1 记忆层
    DMSA_1000 = "DMSA-1000"  # MemoryLayerError 基类
    DMSA_1001 = "DMSA-1001"  # MissingParentError
    DMSA_1002 = "DMSA-1002"  # LineageNotFoundError
    DMSA_1003 = "DMSA-1003"  # InvalidPromptContextError

    # L2 梯度聚合
    DMSA_2000 = "DMSA-2000"  # GradientAggregationError 基类
    DMSA_2001 = "DMSA-2001"  # EmbeddingFailedError
    DMSA_2002 = "DMSA-2002"  # ClusteringFailedError
    DMSA_2003 = "DMSA-2003"  # FusionConflictError
    DMSA_2004 = "DMSA-2004"  # InvalidSampleError

    # L3 工具反向传播
    DMSA_3000 = "DMSA-3000"  # ToolBPError 基类
    DMSA_3001 = "DMSA-3001"  # SchemaValidationError
    DMSA_3002 = "DMSA-3002"  # ProjectionRollbackError
    DMSA_3003 = "DMSA-3003"  # UnsupportedEditError
    DMSA_3004 = "DMSA-3004"  # ToolCallError

    # L4 MCP server
    DMSA_4000 = "DMSA-4000"  # MCPServerError 基类
    DMSA_4001 = "DMSA-4001"  # InvalidRequestError
    DMSA_4002 = "DMSA-4002"  # ToolNotFoundError
    DMSA_4003 = "DMSA-4003"  # TransportError

    # L5 integration
    DMSA_5000 = "DMSA-5000"  # IntegrationError 基类

    # 配置
    DMSA_6000 = "DMSA-6000"  # ConfigError
    DMSA_6001 = "DMSA-6001"  # InvalidConfigValueError


class DMSAError(Exception):
    """所有 hermetic-dmsa 异常的基类。

    Attributes:
        code: 错误码（见 :class:`ErrorCode`）。
        message: 人类可读的错误描述。
        details: 任意附加上下文（用于日志 / MCP 错误响应）。
    """

    code: ErrorCode = ErrorCode.DMSA_0000

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化错误对象, 保存消息与结构化上下文。"""
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 MCP 错误响应 / JSON 日志）。"""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:
        """返回含错误码与消息的可读表示。"""
        return f"{self.__class__.__name__}(code={self.code.value}, message={self.message!r})"


# ============================================================================
# 配置
# ============================================================================


class ConfigError(DMSAError):
    """配置加载 / 解析失败。"""

    code = ErrorCode.DMSA_6000


class InvalidConfigValueError(ConfigError):
    """配置项值非法（如路径不存在、维度 ≤ 0）。"""

    code = ErrorCode.DMSA_6001


# ============================================================================
# 序列化（横切关注点）
# ============================================================================


class SerializationError(DMSAError):
    """JSON / msgpack 序列化失败。"""

    code = ErrorCode.DMSA_0002


# ============================================================================
# L1 记忆层
# ============================================================================


class MemoryLayerError(DMSAError):
    """记忆层操作失败。"""

    code = ErrorCode.DMSA_1000


class MissingParentError(MemoryLayerError):
    """添加节点时某个 parent_id 在 DAG 中不存在。"""

    code = ErrorCode.DMSA_1001


class LineageNotFoundError(MemoryLayerError):
    """查询 lineage 时目标节点不存在。"""

    code = ErrorCode.DMSA_1002


class InvalidPromptContextError(MemoryLayerError):
    """PromptContext 不合法（如缺 prompt_id / agent_id）。"""

    code = ErrorCode.DMSA_1003


# ============================================================================
# L2 梯度聚合
# ============================================================================


class GradientAggregationError(DMSAError):
    """SGA 聚合过程中失败。"""

    code = ErrorCode.DMSA_2000


class EmbeddingFailedError(GradientAggregationError):
    """嵌入计算失败（如输入非字符串、维度不匹配）。"""

    code = ErrorCode.DMSA_2001


class ClusteringFailedError(GradientAggregationError):
    """聚类失败（如全部样本为 outlier、HDBSCAN 不可用）。"""

    code = ErrorCode.DMSA_2002


class FusionConflictError(GradientAggregationError):
    """融合检测到不可调和的冲突（真冲突，应该走 routing）。"""

    code = ErrorCode.DMSA_2003


class InvalidSampleError(GradientAggregationError):
    """GradientSample 字段非法。"""

    code = ErrorCode.DMSA_2004


# ============================================================================
# L3 工具反向传播
# ============================================================================


class ToolBPError(DMSAError):
    """Tool-Use 反向传播过程中失败。"""

    code = ErrorCode.DMSA_3000


class SchemaValidationError(ToolBPError):
    """JSON Schema 不合法或编辑后 schema 校验失败。"""

    code = ErrorCode.DMSA_3001


class ProjectionRollbackError(ToolBPError):
    """AST 投影失败，已自动回滚到原 schema。"""

    code = ErrorCode.DMSA_3002


class UnsupportedEditError(ToolBPError):
    """不支持的编辑指令类型。"""

    code = ErrorCode.DMSA_3003


class ToolCallError(ToolBPError):
    """工具调用结果记录失败。"""

    code = ErrorCode.DMSA_3004


# ============================================================================
# L4 MCP server
# ============================================================================


class MCPServerError(DMSAError):
    """MCP server 内部错误。"""

    code = ErrorCode.DMSA_4000


class InvalidRequestError(MCPServerError):
    """MCP 请求格式不合法（JSON-RPC 解析失败、缺字段）。"""

    code = ErrorCode.DMSA_4001


class ToolNotFoundError(MCPServerError):
    """请求的 tool name 不存在。"""

    code = ErrorCode.DMSA_4002


class TransportError(MCPServerError):
    """stdio 传输层错误。"""

    code = ErrorCode.DMSA_4003


# ============================================================================
# L5 integration
# ============================================================================


class IntegrationError(DMSAError):
    """三方 Agent 集成层错误。"""

    code = ErrorCode.DMSA_5000


# ============================================================================
# 公共导出
# ============================================================================

__all__ = [
    "ClusteringFailedError",
    "ConfigError",
    "DMSAError",
    "EmbeddingFailedError",
    "ErrorCode",
    "FusionConflictError",
    "GradientAggregationError",
    "IntegrationError",
    "InvalidConfigValueError",
    "InvalidPromptContextError",
    "InvalidRequestError",
    "InvalidSampleError",
    "LineageNotFoundError",
    "MCPServerError",
    "MemoryLayerError",
    "MissingParentError",
    "ProjectionRollbackError",
    "SchemaValidationError",
    "SerializationError",
    "ToolBPError",
    "ToolCallError",
    "ToolNotFoundError",
    "TransportError",
    "UnsupportedEditError",
]
