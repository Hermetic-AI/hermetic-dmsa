# Qoder × dmsa CodingMemory Hook

把 Qoder 每一轮编码过程自动沉淀进 dmsa CodingMemory。捕获走 hook，不依赖 agent 自觉。

## 数据流

```
Qoder Stop 事件
  → dmsa_memory_hook.py（读 stdin 的 hook payload）
  → distill.py（按 transcript 行偏移抽取本轮增量 + 脱敏）
  → dmsa_mcp_client.py（子进程 MCP stdio：tools/call dmsa_coding_memory_record）
  → ~/.dmsa/coding-memory.sqlite（scope = 工作区目录名）
```

集成层不 `import dmsa`（AGENTS.md 分层规则：L5 只能经 MCP 协议访问 L4），
所以写库通过 spawn `python -m dmsa.mcp_server.coding_memory` 完成。

## 安装

把下面这段并入 `~/.qoder-cn/settings.json`（保留原有键，备份见 `settings.json.bak-dmsa-hook`）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:\\WorkSpace\\Coding\\agent-memo\\hermetic-dmsa\\.venv\\Scripts\\python.exe",
            "args": [
              "C:\\WorkSpace\\Coding\\agent-memo\\hermetic-dmsa\\integrations\\qoder\\hooks\\dmsa_memory_hook.py"
            ],
            "async": true
          }
        ]
      }
    ]
  }
}
```

`async: true` 让捕获在后台跑，不占用回合。配置在下一回合生效；改完无需重启。

## 每轮写入的内容

一条记录包含：任务原文（人类 prompt）、改动面（本轮 Read/Edit/Write/Bash 命中的文件路径）、
失败（`is_error` 的工具结果摘要）、结论（助手最终正文），以及一行 `关键词:` ASCII 标识符。

- 游标：`~/.dmsa/state.json` 按 sessionId 记住已消费的行号，重复触发不重复写。
- 幂等键：`qoder-hook-<session8>-L<起行>-<sha1前12位>`，落到 `requests.request_id`。
- 脱敏：私钥块、`sk-*`、`Bearer/token`、`api_key|secret|password|access_token` 赋值、32 位以上十六进制串统一替换为 `[REDACTED]`。
- 工具输出正文（`tool_result` 非错误部分）一律不入库，避免把密钥和大段数据灌进记忆。
- 子 agent（`isSidechain`）记录不入库。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DMSA_HOOK_DISABLE=1` | 完全跳过，不读不写 |
| `DMSA_HOOK_DRY_RUN=1` | 只打印蒸馏结果，不写库、不推进游标 |
| `DMSA_CODING_MEMORY_SCOPE` | 覆盖按目录名推导的 scope |
| `DMSA_CODING_MEMORY_PYTHON` | MCP 子进程用哪个 python（默认 `sys.executable`） |
| `DMSA_CODING_MEMORY_HOME` | 状态与日志目录，默认 `~/.dmsa` |
| `DMSA_QODER_PROJECTS` | payload 缺 `transcript_path` 时反查 transcript 的根目录，默认 `~/.qoder-cn/projects` |

## 已知边界

**中文检索已可用，靠的是 L1 换分词。** FTS 虚表原先用 `tokenize='unicode61'`，整段无空格
中文被切成单一 token，纯中文查询零召回。现在 `src/dmsa/memory/coding/schema.py` 用 `trigram`
按字符三元组建索引，查询侧由 `coding/tokenize.py` 展开成三元组，短于 3 字的词项（两字中文词、
`db`）再由 scope 内 LIKE 通道兜底；历史库在初始化时自动重建索引并保留原文与稳定 ID。
实测在真实库（13 个切片）上 `编码过程`、`记忆`、`脱敏`、`游标`、`db` 全部命中，单查询 <3ms。

hook 仍附一行 ASCII `关键词:`，但它服务的是精确通道（文件路径、符号名、报错名），排序比
n-gram 更稳，已不是中文召回的兜底手段。

**MCP 检索没有语义通道。** `dmsa.mcp_server.coding_memory` 固定
`SearchPolicy(dense_weight=0.0)`，近义改写和跨语言概括仍需词面重合；要语义召回得给 server
注入 embedder。

**偶发 Stop 拿到空 stdin。** `~/.dmsa/hook.log` 里出现过 `skip: no transcript, payload keys=[]`，
那一轮不会被捕获。payload 带 `session_id` 但缺 `transcript_path` 时，hook 会按
`~/.qoder-cn/projects/*/<session_id>.jsonl` 反查补回（可用 `DMSA_QODER_PROJECTS` 覆盖根目录）；
stdin 完全为空时没有任何可归属的线索，只能靠拆开的日志判断到底是空输入还是 JSON 截断
（`stdin empty (cwd=...)` / `stdin is not valid JSON: ...`），下一步再定位。

`~/.dmsa/state.json` 的游标按 sessionId 记录已消费行号，`last_request_id` 为 `null` 只表示
server 的 record 响应里没有这个字段，不代表写入失败——写入成功与否看 `recorded session=...`
日志和库里的 `request_id`。

**交互式 MCP 的 scope 需要显式配置。** Qoder 拉起 stdio server 时 cwd 固定在
`.qoder-versions/<版本>`，`dmsa.mcp_server.coding_memory` 用 `Path.cwd().name`
兜底会得到 `0.4.8` 这类无意义 scope。因此 MCP server 条目里必须写死
`DMSA_CODING_MEMORY_SCOPE`。hook 不受影响——它从 payload 的 `cwd` 逐工作区推导。

## 验证

```bash
# 手动跑一轮（不写库）
printf '{"hook_event_name":"Stop","session_id":"<sid>","transcript_path":"<jsonl>","cwd":"<ws>"}' \
  | DMSA_HOOK_DRY_RUN=1 .venv/Scripts/python.exe integrations/qoder/hooks/dmsa_memory_hook.py

# 看捕获日志与已写入的记录
tail ~/.dmsa/hook.log
sqlite3 ~/.dmsa/coding-memory.sqlite "select request_id,scope from requests order by rowid desc limit 5"
```
