# AML Coding 赛道 · 本地真实评测报告（2026-10-23）

- 代码基线：`e75d071`（feat/coding-memory-rsi），Python 3.12.13，Windows 11
- 本报告只包含**实际执行**的评测；未执行项一律标注"未测量"，不填估计值。
- 所有本地分数均为合成代理指标，**不是**官方 150 任务成绩（见文末外部门禁）。

## 1. 赛事文档要点（已核实来源）

| 来源 | 获取方式 | 状态 |
|---|---|---|
| agentmemories.ai/api-guide | HTTPS 抓取 | ✅ 已读 |
| agentmemories.ai/rules | HTTPS 抓取 | ✅ 已读 |
| github.com/AML-memory/agent-memory-leaderboard README_CN.md | `api.github.com` contents API | ⚠️ blob/raw 直连被重置（curl exit 56），API 通道成功 |
| agentmemoryleaderboard.ai/leaderboard/academic/coding | HTTPS 抓取 | ✅ 已读 |

**协议**：参评方自托管 Add/Search HTTP API。Add 为 POST，含 `request_id`/`messages`/`user_id`/`session_id`，返回 `success: true`；Search 为 POST，含 `query`/`options`/`user_id`/`top_k:100`，返回 `data` 数组。认证支持 Token/Bearer/X-Api-Key；`GET /health` 免认证（任意 2xx）。重试：Add 对 408/409/425/429/5xx，Search 对 408/425/429/5xx，4xx 永久错误不重试。

**流程与配额**：部署 API → 提交申请 → 绑定 AML Key → Smoke（1 次/小时，每赛道至多 30 次，不计分）→ Full（每 Key 每赛道至多 2 次，第二次需距首次 30 天）→ 复核发布。

**Coding 赛道**：Cycle 1 使用 CAMBench Coding 150 任务（12 个仓库、1,290 个带时间约束与细粒度相关性标注的历史任务），2026-08-12 已发布；能力维度为 Debug Memory 与 Development Memory；数据受保护、不公开。Cycle 2 于 2026-09-20 开放，当前进行中。公榜归一化为 0–100 分；禁止调参偷答与数据泄漏。

## 2. 合成代理评测（benchmarks.coding，真实执行）

Manifest：`benchmarks/coding/fixtures/mini.json`（CC0 合成，2 任务 × 12 干扰项，噪声 4 级），
SHA-256 `0e51c2b9…91fe1b2`，arms = no_memory + sparse（无需凭据），vector_backend = sqlite。
结果文件：`coding-memory-local-20261023{,-seed42,-seed101}.json/.md`。

| Seed | 总体 Recall | 总体 NDCG | Misleading |
|---|---:|---:|---:|
| 17 | 0.4375 | 0.2851 | 0.0625 |
| 42 | 0.4375 | 0.3452 | 0.0625 |
| 101 | 0.4375 | 0.3832 | 0.0625 |

no_memory arm 各项恒为 0（符合预期，不投喂证据）；sparse arm 分噪声级明细（seed 17）：

| 噪声 | Recall | NDCG | Misleading |
|---|---:|---:|---:|
| low(1) | 1.000 | 0.815 | 0.000 |
| medium_low(3) | 1.000 | 0.500 | 0.000 |
| medium_high(6) | 0.500 | 0.500 | 0.500 |
| high(10) | 1.000 | 0.465 | 0.000 |

**与 2026-10-22 记录基线对比**：基线（`coding-memory-mini.md`）在 high 噪声 sparse 下 Recall 0.500 / Misleading 0.500；本次三 seed 的 medium_high/high 组合整体 Recall 由 0.375 → 0.4375，Misleading 由 0.125 → 0.0625，NDCG 均值上移。差异同时受 seed 影响，不构成算法改进证明，仅作回归监控信号。

**未测量**：localization_accuracy、agent_reported_resolved/test_pass、latency、tokens、cost——本次未挂 `--agent-argv` 编码 agent 包装器，按 runbook 规则保持 null，不采信模型自报。

## 3. 线上服务真实链路验证（Docker 栈，competition profile）

运行容器：`dmsa-memory-memory-1`（127.0.0.1:8000，healthy，`--profile competition --dimension 1024`）、`qdrant`（healthy）。

| 探针 | 结果 | 实测延迟 |
|---|---|---|
| `GET /health` 免认证 | 200 `{"ok":true}` | — |
| 官方 `dmsa-memory-smoke`（add+search，request_id `aml-sim-eval-20261023`） | ✅ passed | — |
| `--search-only` 同 request_id 复读（幂等/持久性） | ✅ passed | — |
| 无凭据 `POST /add` | ✅ 401 拒绝 | — |
| 零词面重叠语义探针：写入"支付回调重试风暴/验签超时"记忆，用"第三方接口超时后订单反复提交导致资损"检索（无任何共享关键词） | ✅ 命中（dense 链路生效证明，词面法不可能召回） | add 1577ms（含嵌入）、search 416ms，top_k=100 |

## 4. MCP 与测试链路

- `dmsa_coding_memory_status`：backend CodingMemory，库 `~/.dmsa/coding-memory.sqlite`，scope `hermetic-dmsa`，available: true。
- `dmsa_coding_memory_search` 对真实项目记忆库检索：返回 5 条含 root cause/hook 验证记录的历史条目（真实数据，非 demo）。
- `pytest tests -k coding`：**66 passed**（覆盖率 38% 未达 80% 门禁仅因 `-k` 过滤了测试集，非失败）。

## 5. 不通/缺失的链路（如实报告）

1. **GitHub 页面直连不通**：`github.com/blob` 与 `raw.githubusercontent.com` 均连接重置；仅 `api.github.com` 可用。已绕行获取 README_CN 全文。
2. **hybrid / full arms 本地 CLI 跑不了**：`benchmarks.coding` 的 `--embedding-model` 仅接受 OpenAI 协议的 `text-embedding-v4`，且需要 `DMSA_BENCH_API_KEY`；本机只有 DashScope 原生端点的 `qwen3.7-text-embedding-flash` 凭据（服务侧在用），无 OpenAI 兼容嵌入 key。→ dense 能力改由 §3 线上探针验证，但**四 arm 完整对照表本次无法产出**。
3. **官方评测不可能在本机完成**：150 任务数据受保护不公开；无 AML Key、无官方 runner、服务未公网可达（现绑定 127.0.0.1）。本报告全部为本地代理指标。
4. **工程结果指标空缺**：resolved/test_passed/localization 需要隔离 checkout 中真实执行测试的 agent 包装器（runbook 第 5 条），本次未搭建。

## 6. 下一步（进入官方评测前）

- 提交评测权限申请拿 AML Key；将 Add/Search 部署为公网 HTTPS 端点并跑官方 Smoke。
- 为 CLI 增加 DashScope 原生适配器（或申请 OpenAI 兼容嵌入 key），补齐 hybrid/full 对照。
- 实现 `--agent-argv` 编码 agent 包装器（隔离 checkout + 真实跑测试），产出可验证的 resolved/test 指标。
