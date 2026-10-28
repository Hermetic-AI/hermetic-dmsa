"""stdio MCP 工具清单与枚举常量.

工具的 JSON Schema 与参数校验分开存放: 清单只描述对外契约, 校验逻辑留在
coding_tools 里, 两个文件都留在项目规范的长度内.
"""

from __future__ import annotations

from typing import Any

from dmsa.mcp_server.coding_tool_schemas_ext import EXTENDED_TOOLS

FEEDBACK_KINDS: tuple[str, ...] = (
    "coarse_chunk",
    "exact_miss",
    "identifier_confusion",
    "missing_context",
    "missing_relation",
    "missing_structure",
    "redundancy",
    "stale_evidence",
    "unknown",
)
OUTCOMES: tuple[str, ...] = ("success", "failure")
# 程序性记忆支持的动作.
PROCEDURE_ACTIONS: tuple[str, ...] = ("record", "search")

CORE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "dmsa_coding_memory_search",
        "description": "检索本项目过去的缺陷修复、设计决策和验证经验；编码前调用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务、报错、文件或符号"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "task_id": {"type": "string", "description": "可选任务标识，用于关联任务结果"},
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dmsa_coding_memory_record",
        "description": "在任务验证后沉淀可复用的代码经验，需明确区分事实、决策和验证结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "稳定幂等键，如任务/提交标识"},
                "session_id": {"type": "string", "description": "任务或会话标识"},
                "content": {"type": "string", "description": "问题、根因、改动、测试及结果"},
                "author": {
                    "type": "string",
                    "description": "可选作者标识，团队作用域下用于识别跨作者的写冲突",
                },
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": ["request_id", "session_id", "content"],
        },
    },
    {
        "name": "dmsa_coding_memory_status",
        "description": "检查当前 CodingMemory 数据库和项目隔离范围。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dmsa_coding_memory_feedback",
        "description": (
            "在任务验证后提交检索反馈，将检索轨迹与成败关联；新反馈会触发受回放门禁保护的策略进化。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "search 返回的 trace_id"},
                "outcome": {"type": "string", "enum": list(OUTCOMES)},
                "kind": {"type": "string", "enum": list(FEEDBACK_KINDS), "default": "unknown"},
                "relevant_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "确认有用的证据 ID；必须来自同一 scope 的真实命中",
                },
            },
            "required": ["trace_id", "outcome"],
        },
    },
    {
        "name": "dmsa_coding_memory_task_result",
        "description": "上报真实任务结果，供后续任务级评测使用；不参与根因推断。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务稳定标识"},
                "trace_id": {"type": "string", "description": "关联的检索轨迹 ID"},
                "outcome": {"type": "string", "enum": list(OUTCOMES)},
                "tests_passed": {"type": "integer", "minimum": 0, "default": 0},
                "files_changed": {"type": "array", "items": {"type": "string"}, "default": []},
                "tokens": {"type": "integer", "minimum": 0, "default": 0},
                "latency_ms": {"type": "integer", "minimum": 0, "default": 0},
                "detail": {"type": "string", "default": ""},
            },
            "required": ["task_id", "trace_id", "outcome"],
        },
    },
    {
        "name": "dmsa_coding_memory_governance",
        "description": "查看记忆条目的信任度与观测计数，或淘汰长期未使用的低信任条目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
                "forget": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 时执行遗忘淘汰，淘汰条目不再进入检索结果",
                },
                "floor": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.1,
                    "description": "淘汰阈值：信任度低于该值的条目会被淘汰",
                },
                "min_age_days": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 30,
                    "description": "最短闲置天数：近期被使用过的条目不会被淘汰",
                },
            },
        },
    },
    {
        "name": "dmsa_coding_memory_procedure",
        "description": (
            "程序性记忆: record 从含显式步骤的正文沉淀可复用流程, "
            "search 按触发语境匹配已沉淀的流程。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(PROCEDURE_ACTIONS),
                    "default": "search",
                    "description": "record 沉淀流程, search 匹配流程",
                },
                "request_id": {"type": "string", "description": "record 用的幂等键"},
                "session_id": {"type": "string", "description": "record 用的会话标识"},
                "content": {
                    "type": "string",
                    "description": "record 用的正文, 需含至少两条显式有序步骤",
                },
                "query": {"type": "string", "description": "search 用的触发语境"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离流程"},
            },
            "required": [],
        },
    },
]

# 对外暴露的完整工具清单: 核心工具在前, 扩展工具在后.
TOOLS: list[dict[str, Any]] = [*CORE_TOOLS, *EXTENDED_TOOLS]
