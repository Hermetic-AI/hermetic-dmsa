# Coding Memory local self-generated evaluation

> 本地合成任务与本地 pytest 判定，不是官方成绩，`official_score` 固定为 null。

Seed: `101`; 每任务最多尝试 `10` 个补丁。

| Arm | Runs | Resolved | Recall | Mean hits | Mean tokens | Failed attempts |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 15 | 0.6 | 0.6 | 22.3333 | 5387.5333 | 53 |
| tuned | 15 | 1.0 | 1.0 | 68.1333 | 16929.3333 | 33 |

| Arm | Noise | Resolved | Recall | Mean hits | Mean tokens |
|---|---|---:|---:|---:|---:|
| baseline | relevant_only | 0.3333 | 0.3333 | 5.6667 | 1228.0 |
| baseline | L1 | 0.3333 | 0.3333 | 21.3333 | 5126.3333 |
| baseline | L2 | 0.3333 | 0.3333 | 28.3333 | 6947.0 |
| baseline | L3 | 1.0 | 1.0 | 28.0 | 6817.0 |
| baseline | L4 | 1.0 | 1.0 | 28.3333 | 6819.3333 |
| tuned | relevant_only | 1.0 | 1.0 | 10.6667 | 2530.6667 |
| tuned | L1 | 1.0 | 1.0 | 30.0 | 7358.0 |
| tuned | L2 | 1.0 | 1.0 | 100.0 | 24916.6667 |
| tuned | L3 | 1.0 | 1.0 | 100.0 | 24921.6667 |
| tuned | L4 | 1.0 | 1.0 | 100.0 | 24919.6667 |
