# Coding Memory synthetic proxy evaluation

> This is not an official competition score. Synthetic retrieval metrics only.

Manifest SHA-256: `0e51c2b973fefcbd4412bfda78f65bf5eb1fd6ac38a9c764b0118e1f291fe1b2`

| Metric | Value |
|---|---:|
| agent_reported_resolved_rate | not measured |
| agent_reported_test_pass_rate | not measured |
| localization_accuracy | not measured |
| evidence_recall | 0.4375 |
| ndcg | 0.3452324383928644 |
| misleading_memory_rate | 0.0625 |
| latency_seconds | not measured |
| mean_tokens | not measured |
| total_cost_usd | not measured |

## By arm and noise level

| Arm | Noise | Tasks | Recall | NDCG | Misleading |
|---|---|---:|---:|---:|---:|
| no_memory | low | 2 | 0.000 | 0.000 | 0.000 |
| no_memory | medium_low | 2 | 0.000 | 0.000 | 0.000 |
| no_memory | medium_high | 2 | 0.000 | 0.000 | 0.000 |
| no_memory | high | 2 | 0.000 | 0.000 | 0.000 |
| sparse | low | 2 | 1.000 | 1.000 | 0.000 |
| sparse | medium_low | 2 | 1.000 | 0.815 | 0.000 |
| sparse | medium_high | 2 | 1.000 | 0.631 | 0.000 |
| sparse | high | 2 | 0.500 | 0.315 | 0.500 |
