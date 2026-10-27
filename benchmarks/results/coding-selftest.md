# Coding Memory local self-generated evaluation

> 本地合成任务与本地 pytest 判定，不是官方成绩，`official_score` 固定为 null。

Seed: `17`; 每任务最多尝试 `10` 个补丁。

| Arm | Runs | Resolved | Recall | Mean hits | Mean tokens | Failed attempts |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 15 | 0.6667 | 0.6667 | 22.6 | 5401.4667 | 66 |
| tuned | 15 | 1.0 | 1.0 | 68.1333 | 16893.4 | 51 |

| Arm | Noise | Resolved | Recall | Mean hits | Mean tokens |
|---|---|---:|---:|---:|---:|
| baseline | relevant_only | 0.3333 | 0.3333 | 5.6667 | 1221.6667 |
| baseline | L1 | 0.3333 | 0.3333 | 21.6667 | 5215.6667 |
| baseline | L2 | 0.6667 | 0.6667 | 28.3333 | 6822.0 |
| baseline | L3 | 1.0 | 1.0 | 28.6667 | 6910.6667 |
| baseline | L4 | 1.0 | 1.0 | 28.6667 | 6837.3333 |
| tuned | relevant_only | 1.0 | 1.0 | 10.6667 | 2524.0 |
| tuned | L1 | 1.0 | 1.0 | 30.0 | 7350.6667 |
| tuned | L2 | 1.0 | 1.0 | 100.0 | 24926.6667 |
| tuned | L3 | 1.0 | 1.0 | 100.0 | 24943.3333 |
| tuned | L4 | 1.0 | 1.0 | 100.0 | 24722.3333 |
