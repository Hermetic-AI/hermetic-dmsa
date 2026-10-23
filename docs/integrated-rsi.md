# DMSA + RSI

Compose 默认打开 `DMSA_RSI_ENABLED=true` 与 `DMSA_RSI_MODELS=true`。
memory 统一管理记忆和策略；agent-mcp 通过 HTTP 桥接，rsi-advisor 通过统一 API 提交进化请求。
旧独立数据库卷保留。

1. `POST /search` 返回 `trace_id` 和 `data`。
2. `POST /feedback` 传 `user_id`、`trace_id`、`outcome`（success/failure）、
   `kind`（unknown/exact_miss/redundancy/missing_context）、`relevant_ids`。
   相关 ID 必须属于该 scope，允许标注漏召回来源。重复反馈幂等，冲突拒绝。
3. 新反馈自动触发进化。同类失败至少两次且有相关来源标签才会生成候选。
4. 决策模型只能选择确定性候选，文本模型解释。仅策略和诊断计数发送给模型。
5. 最新至多 100 个带标签开发样本进行固定 top_k=5 的配对回放，保持相同证据预算。
   所有样本召回不下降且至少一个改善才激活。写锁下复核基线和语料指纹。
6. 活动策略按 scope 持久化，下一次搜索立即使用。`POST /rsi/rollback` 传 `user_id`、
   `version`，恢复该 scope 已保存的基线或通过策略。

`POST /rsi/evolve`（别名 `/rsi/propose`）接收 `{"user_id":"项目名"}`，用于重试。
模型失败时反馈仍保存，并显式记录 model_fallback，使用原有确定性候选继续相同回放门禁。
事件保留错误类型和 HTTP 状态，不保存原始 provider 错误；可以 evolve 重试模型。
所有接口复用实例 API Key，其持有者具有实例级管理权限；scope 不是独立租户认证。

页面 RSI 区域每十秒读取最新 100 条事件，展示模型名、调用状态、耗时、候选、回放、
活动策略和回滚版本。调用成功不意味着策略改善；这是开发集检索代理指标，不代表任务通过率。

Codex 支持 `DMSA_CODING_MEMORY_URL` 与 `DMSA_ENV_FILE`。重新加载 MCP 连接后，
search/record/feedback 进入统一服务。旧本地库保留。

```powershell
'{"user_id":"hermetic-dmsa"}' | docker compose --profile rsi run --rm -T rsi-advisor
docker compose --profile mcp --profile rsi up -d --build --wait
```

轨迹存储本地查询正文，需保护数据库卷；查询不发送给策略模型。
sparse-dev 仍是稀疏检索；向量模型需要 competition profile。
