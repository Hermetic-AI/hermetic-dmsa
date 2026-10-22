# Coding Memory synthetic proxy evaluation

> This is not an official competition score. Synthetic retrieval metrics only.

Manifest SHA-256: `0e51c2b973fefcbd4412bfda78f65bf5eb1fd6ac38a9c764b0118e1f291fe1b2`

| Metric | Value |
|---|---:|
| agent_reported_resolved_rate | not measured |
| agent_reported_test_pass_rate | not measured |
| localization_accuracy | not measured |
| evidence_recall | 0.375 |
| ndcg | 0.2581831095982161 |
| misleading_memory_rate | 0.125 |
| latency_seconds | not measured |
| mean_tokens | not measured |
| total_cost_usd | not measured |

## By noise level

- `low`: evidence recall=0.500, misleading-memory=0.000
- `medium_low`: evidence recall=0.500, misleading-memory=0.000
- `medium_high`: evidence recall=0.250, misleading-memory=0.250
- `high`: evidence recall=0.250, misleading-memory=0.250
