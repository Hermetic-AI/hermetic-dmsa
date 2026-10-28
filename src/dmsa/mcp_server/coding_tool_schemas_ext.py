"""stdio MCP 工具清单的扩展部分.

工具的 JSON Schema 与参数校验分开存放: 清单只描述对外契约, 校验逻辑留在
coding_tools 里. 清单本身拆成核心与扩展两个模块, 两者都留在项目规范的长度内.
"""

from __future__ import annotations

from typing import Any

# 写冲突仲裁支持的动作.
CONFLICT_ACTIONS: tuple[str, ...] = ("list", "resolve")
# 仲裁结论: 保留新写入、保留既有写入, 或两者并存且不做信任惩罚.
CONFLICT_RESOLUTIONS: tuple[str, ...] = ("keep_new", "keep_rival", "keep_both")
# 冲突状态过滤.
CONFLICT_STATUSES: tuple[str, ...] = ("open", "resolved")

EXTENDED_TOOLS: list[dict[str, Any]] = [
    {
        "name": "dmsa_coding_memory_effectiveness",
        "description": (
            "度量记忆的实际有效性：检索侧标签召回率与任务侧分组成功率；"
            "样本不足时如实留空，任务侧为观察性对比而非受控 A/B。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dmsa_coding_memory_conflicts",
        "description": (
            "团队作用域的写冲突仲裁: list 列出待仲裁的跨作者冲突, "
            "resolve 按结论关闭冲突, 并把结论写回双方的信任计数。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(CONFLICT_ACTIONS),
                    "default": "list",
                    "description": "list 列出冲突, resolve 仲裁一条冲突",
                },
                "conflict_id": {"type": "string", "description": "resolve 用的冲突 ID"},
                "resolution": {
                    "type": "string",
                    "enum": list(CONFLICT_RESOLUTIONS),
                    "description": "resolve 用的仲裁结论",
                },
                "status": {
                    "type": "string",
                    "enum": list(CONFLICT_STATUSES),
                    "default": "",
                    "description": "list 用的状态过滤, 空字符串表示全部",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": [],
        },
    },
    {
        "name": "dmsa_coding_memory_hybrid_search",
        "description": (
            "稠密混合检索: 按通道归一化融合词面与稠密召回, 再按跨通道一致性与重复度做有界重排; "
            "返回每条证据的通道贡献、一致通道数与重排增量。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务、报错、文件或符号"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dmsa_coding_memory_recall",
        "description": (
            "带召回解释的检索: 返回每条证据的命中通道、命中词项与定位信息, "
            "并可做多跳迭代扩展, 用首轮命中的路径与符号继续召回相关证据。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务、报错、文件或符号"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "hops": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3,
                    "default": 1,
                    "description": "迭代跳数: 1 表示单跳, 更大表示用首轮命中的定位信号继续召回",
                },
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dmsa_coding_memory_consolidate",
        "description": (
            "记忆合并: 把同一作用域里反复出现的同一条经验收敛成一条通用规则, "
            "派生条目保留全部来源 ID, 原始证据不被删除。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
                "min_group": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 20,
                    "default": 2,
                    "description": "至少重复几次才合并成一条规则",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 500,
                    "description": "参与合并的最近切片条数上限",
                },
            },
            "required": [],
        },
    },
    {
        "name": "dmsa_coding_memory_transfer",
        "description": (
            "跨仓库迁移: 把不含具体路径与符号的通用规则复制到另一个仓库的子域, "
            "被拒绝的规则与泄漏项会在报告里列出。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_repo": {"type": "string", "description": "来源仓库名"},
                "target_repo": {"type": "string", "description": "目标仓库名，必须与来源不同"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 500,
                    "description": "参与迁移的最近切片条数上限",
                },
            },
            "required": ["target_repo"],
        },
    },
    {
        "name": "dmsa_coding_memory_search_as_of",
        "description": (
            "时序回看检索: 按作用域写入序号或条目写入时刻检索历史证据, "
            "复现某一时刻记忆库当时能看到的内容; 返回结果同时给出作用域当前写入序号。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务、报错、文件或符号"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "as_of_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "作用域内的写入序号上界, 含该次写入",
                },
                "as_of_timestamp": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "条目写入时刻的 Unix 秒上界, 含该时刻",
                },
                "repo": {"type": "string", "description": "可选仓库名，按仓库子域隔离证据"},
            },
            "required": ["query"],
        },
    },
]
