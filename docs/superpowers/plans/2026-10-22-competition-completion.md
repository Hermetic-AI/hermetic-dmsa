# Competition Completion Plan

**Goal:** Turn `hermetic-dmsa` into a standalone, open-source, one-command deployed Coding Memory system with production retrieval, noise-resistant engineering experiences, and reproducible local/real-model evaluation.

## Global constraints

- Preserve the public Add/Search contract and existing APIs.
- Chinese docstrings, English identifiers, typed `DMSAError` failures, no `print()` in library code.
- Production Python files stay at or below 300 lines.
- Strict layer direction: memory -> PMD; gradient -> memory; toolbp -> gradient/memory; server -> lower layers.
- New behavior follows TDD and receives normal/error/boundary coverage.
- Public API changes update `docs/api-reference.md`; algorithm changes update `docs/paper-mapping.md` and `docs/algorithms/`.
- Never log secrets or source payloads. Scope filtering happens before ranking.
- No fabricated leaderboard or model scores; every reported number must come from a persisted run artifact.

## Task 1: Standalone packaging and Docker Compose deployment

Deliver a standalone source/build story that no longer requires an undocumented sibling checkout. Add a production Docker image, Compose stack, persistent volumes, health checks, non-root execution, secrets/config examples, and smoke tooling for `/health`, `/add`, and `/search`. The sparse profile must run with one command without paid credentials; competition profile must fail closed when required credentials are absent. Add contract, restart-persistence, and container-facing tests plus deployment/open-source documentation.

Acceptance: `docker compose up -d --build` starts a healthy sparse service from the documented checkout; smoke succeeds; restart preserves data; image contains license/notice metadata; existing tests remain green.

## Task 2: Production scoped vector backend and compliant model integration

Add an optional production vector backend suitable for large corpora, using Qdrant in Compose with native scope filtering. Preserve the bounded SQLite dense implementation as a reference backend. Implement batched `text-embedding-v4` ingestion/query integration, dimension/fingerprint validation, retry/backoff, typed provider failures, and no silent fallback. Hybrid retrieval must remain deterministic after candidate acquisition.

Acceptance: unit/integration tests cover scope isolation, restart, dimension mismatch, provider failure, batch behavior, and hybrid fusion; a local fake embedding/Qdrant-compatible test requires no paid API; Compose exposes Qdrant only on the internal network by default.

## Task 3: Noise-resistant coding experience layer

Create provenance-preserving engineering experience cards for bug fixes and feature enhancements. Extract paths, symbols, errors, symptoms, root-cause statements, change strategy, failed attempts, applicability conditions, version hints, and validation evidence without executing source content. Add task routing, negative-evidence handling, version/condition penalties, reranking, diversity/facet quotas, and budgeted evidence packing. Raw evidence stays immutable and every card maps back to source spans.

Acceptance: adversarial tests prove lexical lookalikes, stale APIs, failed attempts, opposite conditions, and duplicate logs do not displace relevant evidence; bug-fix and feature queries both retain useful facets; provenance survives restart.

## Task 4: Reproducible four-level noise and Coding Agent evaluation harness

Build `benchmarks/coding/` with manifest-driven tasks, four configurable proxy noise levels, deterministic history construction, no-memory/sparse/hybrid/full arms, paired runs, resumable artifacts, and metrics for resolved rate, bug/feature slices, localization, evidence recall/nDCG, misleading-memory rate, token/cost/latency, and noise degradation. Provide a subprocess coding-agent adapter plus an OpenAI-compatible adapter that only activates with explicit credentials. Add a small licensed synthetic fixture suite and CLI.

Acceptance: offline fixture evaluation produces machine-readable JSON and Markdown reports; paired comparisons reject missing/NaN/overbudget runs; model calls are mock-tested; run artifacts record commit/config/model/seed hashes.

## Task 5: Release hardening, CI, security, and end-to-end validation

Add CI jobs for tests, coverage, Ruff, mypy, Docker build, Compose smoke, dependency/license checks, and benchmark fixture regression. Add `.env.example`, deployment runbook, threat model, data deletion/retention guidance, contribution templates, release checklist, method card, reproducibility manifest, and operational observability needed for public deployment. Validate concurrency, restart, idempotency, scope deletion, and bounded load.

Acceptance: full tests and quality gates pass; Docker/Compose smoke is fresh; offline benchmark report is generated; all Python file size and layer constraints pass; final whole-branch review has no Critical or Important findings.

## External execution gate

When compliant credentials are present, run a three-task real-model dry run and then the configured pilot. When an AML key and public HTTPS endpoint are present, run official Smoke before any Full evaluation. Missing credentials are reported as an external gate, never replaced by invented scores.
