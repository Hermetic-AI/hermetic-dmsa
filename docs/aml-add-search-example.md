# AML 参评表单填写与 Add/Search 调用示例（2026-10-23）

> 依据官方 API 指南（`docsrc/api_guide_full.txt` 为官网全文提取件）整理。
> 文中 `Token` 认证方案已在本地 dmsa 服务上实测通过（Add 与 Search 均返回契约要求的结构）。

## 1. 表单填写值

| 字段 | 填写 | 说明 |
|---|---|---|
| 系统名称 | `hermetic-dmsa CodingMemory` | 建议用真实系统名，非占位 |
| 版本名称 | `v1.0`（提交时改为冻结的 git commit，如 `v1.0-e75d071`） | 官方要求成绩绑定系统版本 |
| Add API 地址 | `https://你的域名/v1/memories/add` | ⚠️ 必须是**公网 HTTPS**；官方明确拒绝回环/私有/链路本地地址。当前服务绑在 `127.0.0.1:8000`，直接填会被网络校验拒掉 |
| Search API 地址 | `https://你的域名/v1/memories/search` | 同上；路径可自定，schema 固定不随路径变化 |
| 认证方式 | `Authorization: Token` | 本服务实测 Token/Bearer/X-Api-Key 三种都接受；选 Token 则表单 Key 必须与之一致，不一致官方判 401 且不重试 |
| 记忆系统 Key | `DMSA_MEMORY_API_KEY` 的值 | 走平台加密通道提交，**不要写进仓库或截图** |
| Add 状态地址（可选） | **建议留空** | 本服务是同步契约（写完成才返回 200），没有 `/add/status/{task_id}` 端点；只有绑定了该地址才允许返回 202，否则 202 即契约错误 |
| 健康检查地址 | `https://你的域名/health` | 免认证 GET，返回任意 2xx 即可；本服务返回 `{"ok": true}` |

## 2. Add 调用（官方固定 schema）

```bash
curl -X POST "https://你的域名/v1/memories/add" \
  -H "Content-Type: application/json" \
  -H "Authorization: Token <MEMORY_SYSTEM_KEY>" \
  -d '{
    "request_id": "eval:run_abc123:cambench:repo7-task42:chunk-0",
    "messages": [
      {
        "role": "user",
        "timestamp": 1704067200000,
        "content": "src/cache.py load_cache race: version check outside lock; fix compares version under lock; validated with pytest tests/test_cache.py"
      }
    ],
    "user_id": "eval:run_abc123:cambench:repo7",
    "session_id": "eval:run_abc123:repo7-session-3"
  }'
```

期望响应（HTTP 200，三个 ID 必须与请求**逐字节一致**，`success` 必须是布尔 `true`）：

```json
{
  "success": true,
  "request_id": "eval:run_abc123:cambench:repo7-task42:chunk-0",
  "user_id": "eval:run_abc123:cambench:repo7",
  "session_id": "eval:run_abc123:repo7-session-3"
}
```

字段约束：

- `request_id`：必填，本次写入请求唯一标识；重试和断点续跑必须保持同一 `request_id` 与请求体。
- `messages`：必填有序数组，每条含 `role` 和非空 `content`；文本/代码用字符串，多模态可用 text/image_url 内容数组；`timestamp` 可选，Unix 毫秒。
- `user_id`：必填，Search 唯一使用的检索隔离边界，写入与检索必须完全一致。
- `session_id`：必填，仅用于组织来源会话，**不作为 Search 筛选条件**。
- 官方不发送 `metadata` / `app_id` / `agent_id` / `async_mode`；Add 必须在持久化且"立即可检索"后才返回 200，单请求允许最长 30 分钟。
- 普通文本按 20 条消息或 2,000 词（冻结 Adapter 计数）确定性分段；代码赛道保留仓库/会话轨迹。

## 3. Search 调用

```bash
curl -X POST "https://你的域名/v1/memories/search" \
  -H "Content-Type: application/json" \
  -H "Authorization: Token <MEMORY_SYSTEM_KEY>" \
  -d '{
    "query": "cache refresh 后读到旧值是什么根因？",
    "options": [],
    "user_id": "eval:run_abc123:cambench:repo7",
    "top_k": 100
  }'
```

期望响应（`data` 按相关性降序、条数 ≤ top_k，每条必有非空 `id`/`content`；无结果返回空数组）：

```json
{
  "data": [
    {
      "id": "4effebc297d06a63...",
      "content": "[request=token-scheme-test-001 session=tok-sess ...]",
      "score": 0.87,
      "created_at": "2026-10-23T07:30:00Z"
    }
  ]
}
```

字段约束：

- `query`：必填，基准原文，不得替换为最终答案或使用金标。
- `options`：可选，仅选择题在顶层传选项文本数组，开放题不发送；现行规范不发送 `filters` / `rerank` / `keyword_search`。
- `top_k`：必填，正式外部评测固定为 100；**返回超量或非法条目判契约错误，不会静默截断**。
- `score` / `created_at` 可选；未声明字段（如本服务的 `trace_id`）平台只读取声明字段，会被忽略——无害，且 `trace_id` 正好可配合 AGENTS.md 的 feedback 链路。

## 4. 健康检查

```bash
curl "https://你的域名/health"   # 无 Authorization 头
# 任意 2xx 即通过；本服务实测返回 {"ok": true}
```

## 5. 本地实测记录（Token 方案）

2026-10-23 对 `127.0.0.1:8000`（competition profile）执行：

- Add（`Authorization: Token`，request_id `token-scheme-test-001`）→ 200，`success:true`，三 ID 原样回显 ✅
- Search（同 Key，top_k=100，options 空）→ 200，`data` 命中该条记忆 ✅

## 6. 重试与错误处理（官方行为，实现需配合）

- Add：网络错误及 408/409/425/429/500/502/503/504/524 有界重试；409 不代表成功，须保持同一 `request_id` 与请求体。
- Search：网络错误及 408/425/429/5xx 有界重试；400/401/403/404/409/422 永久错误不重试。
- 即使 HTTP 200，只要 Add 未返回 `success=true`、ID 未回显，或 Search 缺 `data` 数组、条目缺 `id`/`content`，该阶段立即失败。

## 7. 提交前硬前提与配额

1. 服务必须从 `127.0.0.1` 换成公网 HTTPS（反代 + TLS），否则准入网络校验不通过。
2. 开源方法榜（Academic）强制 `text-embedding-v4` 嵌入 + `gpt-4o-mini` LLM 组件（rerank 不限）；当前服务用的是 DashScope `qwen3.7-text-embedding`，**走开源榜前需换模型**，商业产品榜不受限。
3. 评测数据禁止用于训练/微调/产品分析/数据集重建，任务完成后 30 天内删除。
4. 配额：Smoke 每小时 1 次、本届每赛道最多 30 次；Full 每 Key 每赛道最多 2 次，第 2 次须距首次完成 30 天。
