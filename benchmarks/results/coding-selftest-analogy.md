# Coding Memory local self-generated evaluation

> 本地合成任务与本地 pytest 判定，不是官方成绩，`official_score` 固定为 null。

Seed: `17`; 每任务最多尝试 `10` 个补丁。
历史模式: `analogy`; 外部 Agent 驱动: `False`。

| Arm | Runs | Resolved | Answer present | Analogy present | Mean hits | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 15 | 1.0 | 0.0 | 1.0 | 21.3333 | 4950.0667 |
| tuned | 15 | 1.0 | 0.0 | 1.0 | 4.1333 | 678.0 |

| Arm | Noise | Resolved | Answer present | Analogy present | Mean hits |
|---|---|---:|---:|---:|---:|
| baseline | relevant_only | 1.0 | 0.0 | 1.0 | 4.0 |
| baseline | L1 | 1.0 | 0.0 | 1.0 | 20.0 |
| baseline | L2 | 1.0 | 0.0 | 1.0 | 28.0 |
| baseline | L3 | 1.0 | 0.0 | 1.0 | 27.3333 |
| baseline | L4 | 1.0 | 0.0 | 1.0 | 27.3333 |
| tuned | relevant_only | 1.0 | 0.0 | 1.0 | 4.3333 |
| tuned | L1 | 1.0 | 0.0 | 1.0 | 4.3333 |
| tuned | L2 | 1.0 | 0.0 | 1.0 | 4.0 |
| tuned | L3 | 1.0 | 0.0 | 1.0 | 4.0 |
| tuned | L4 | 1.0 | 0.0 | 1.0 | 4.0 |
