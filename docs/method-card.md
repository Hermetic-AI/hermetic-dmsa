# Method card: Coding Memory noise-resilient retrieval

## Intended use

An experimental, provenance-aware memory layer for software engineering agents.
It stores ordered task/agent/tool messages, retrieves historical evidence in a
caller-defined scope, and can provide deterministic experience facets for
bug-fix/feature queries. It does not edit code, decide whether a patch is
correct, or run tests.

## Method

- SQLite is the source of truth for messages, chunks, scope, and provenance.
- Retrieval combines exact terms and FTS; an optional embedder enables dense
  candidates from a bounded SQLite reference scan or a Qdrant ANN index.
- Candidate ranks are fused deterministically, checked against SQLite scope,
  reranked with a small opt-in applicability prior, deduplicated, and packed
  within explicit evidence budgets.
- Experience cards are deterministic views over explicit source text. They do
  not invent causes, fixes, conditions, or test results. Every card maps to a
  source message/span.

## Evaluation

`benchmarks/coding/` currently includes a small CC0 synthetic proxy fixture
with bug and feature tasks plus four nested distractor-count levels. It
measures retrieval recall/NDCG, misleading-memory rate, localization against a
held-out path label, and optional agent reports. The direct API adapter's
resolution/test fields are self-reported. No run against the official 150-task
suite is included in this repository.

## Limitations and risks

- Synthetic lexical distractors do not reproduce official noise construction
  or task difficulty.
- Similarity/retrieval scores are not probabilities of task success.
- Historical records can be stale, wrong, or prompt-injection-bearing; memory
  must be treated as untrusted evidence and checked against current code/tests.
- A single HTTP API key currently spans every supplied `user_id`; this is not a
  multi-tenant authorization system.
- Dense mode sends source text to the configured embedding provider.
- No published model leaderboard result or official competition score is
  claimed here.

## Reproducibility

Every local artifact contains manifest and configuration hashes, seed,
repository commit when available, arm/noise slices, and per-run measurements.
See [`competition-runbook.md`](competition-runbook.md) and
[`security-and-data.md`](security-and-data.md).
