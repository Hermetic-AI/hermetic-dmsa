# benchmarks/ — hermetic-dmsa 实验框架

验证 `analysis/02`（SGA）和 `analysis/03`（ToolBP）两篇分析文档中声称的量化收益。
需要一枚 LLM API key（任何 OpenAI 兼容 endpoint）。

## 运行

```bash
export DMSA_BENCH_API_KEY=sk-...
export DMSA_BENCH_BASE_URL=https://.../compatible-mode/v1
# 可选：DMSA_BENCH_CHAT_MODEL（默认 qwen3.5-flash）
#       DMSA_BENCH_EMBEDDING_MODEL（默认 text-embedding-v3，dim 1024）

PYTHONPATH=src python benchmarks/bench_toolbp.py         # ~2 分钟
PYTHONPATH=src python benchmarks/bench_sga_conflict.py   # ~60 分钟
```

结果落盘 `benchmarks/results/*.json`。

## Benchmark 1：SGA vs TextGrad 式归约（bench_sga_conflict.py）

冲突反馈下的 prompt 优化。两类用户段偏好直接冲突（[quick] ≤40 词 /
[deep-dive] ≥300 词），初始 prompt 为折中版（"每题恰好 100 词"），首轮两段
同时失败。评判 = 规则 word count（零 LLM judge 噪声）。

- baseline 臂：当轮反馈拼接 → LLM 归并为**单条无条件指令**（TextGrad
  `_check_and_reduce_gradients` 的 ephemeral batch 语义，无记忆）
- SGA 臂：滚动 2 轮窗口累积反馈 → SGAAggregator（OpenAI 嵌入 + LLMFusion）
  → LLM 改写（允许条件分支；DMSA 的记忆持久化 + 聚合语义）

**结果**（qwen3.5-flash，2026-08-10，`results/sga_conflict.json`）：

| 指标 | baseline | SGA |
|---|---|---|
| 6 轮轨迹 | 全部 0%/0%（折中陷阱，从未收敛） | 第 2 轮收敛 100%/100%，稳定 |
| LLM 调用 | 42 | 20（少 52%） |

另一次运行（偏置"始终详尽"初始 prompt，deep 阈值 120 词）复现了另一种失败
模式：baseline 在"全员简洁 ↔ 全员详尽"间震荡（gap_stdev 0.737，3/6 轮极化，
6 轮未收敛），SGA 第 2 轮收敛（gap_stdev 0.373，震荡降 49%）。

**已知局限**：两次运行中 fallback 单链接聚类都把两类冲突反馈并成 1 个簇
（clusters=1），ConflictDetector 的冲突→条件分支路径未被触发；条件分支实际
由改写 LLM 从融合文本中生成。这意味着 §5 的"聚类 + 冲突检测"机制有待更精细
的实验（调 conflict_threshold / 换 HDBSCAN / 更多反馈类型）单独验证。

## Benchmark 2：ToolBP vs prompt-only（bench_toolbp.py）

工具 get_weather(city, date)，date 必须严格 YYYY-MM-DD；查询带相对日期诱导
格式错误。prompt-only 臂把错误堆进 system prompt（Reflexion 风格）；ToolBP 臂
用 ToolCallCritic → ToolBPEngine.project 直接改 schema。

**结果**（`results/toolbp_format.json`）：平均格式错误率 90% → 30%，
**降低 67%**；prompt-only 臂堆错误后反而恶化（83%→100%）。ToolBP 臂残留
17–33% 错误是相对日期计算错误（格式对、日期错），不属 schema 描述能解决的范围。

## 设计迭代记录（为何跑了六版）

1. v1：中性初始 prompt → 两段天然全过，无冲突产生
2. v2：偏置 prompt，但基线允许"改写满足所有反馈"→ 强模型直接写出条件规则，
   非 TextGrad 归约语义
3. v3：基线改单指令归约，但 deep 阈值 120 词太易（简洁指令下仍达标），
   震荡在分数上不可见
4. v4：无记忆基线 + deep≥300 词 → 震荡可见（上述第二组数据）
5. v5：折中初始 prompt（首轮双失败）→ 折中陷阱数据，但 90 分钟超时被杀
6. v6：早停 + max_tokens 收紧 → 完整落盘（上述第一组数据）
