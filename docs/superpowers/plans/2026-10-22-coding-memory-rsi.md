# Coding Memory + RSI Implementation Plan

> **For agentic workers:** Execute task by task with subagent-driven-development and review each task. User approved implementation; no further design approval needed.

**Goal:** Deliver a durable Coding Memory Add/Search service and auditable offline retrieval-policy evolution without changing the frozen baseline.

**Architecture:** L1 owns scoped source evidence, deterministic episode extraction, SQLite FTS, and injected embedding protocols. L2 owns immutable policies and paired-replay promotion. L4 exposes optional HTTP endpoints and injects model clients. Existing MCP public imports remain compatible.

**Tech Stack:** Python >=3.10, PMD, SQLite FTS5, optional FastAPI/uvicorn/OpenAI client, pytest/ruff/mypy.

## Global Constraints

- Chinese docstrings; English identifiers; each Python file <=300 lines.
- Strict downward imports, L1 does not import L2 concrete clients.
- Public API normal/error/boundary tests and docs/api-reference.md.
- Algorithm documentation in docs/paper-mapping.md and docs/algorithms/.
- Errors use DMSAError subclasses. No print in new source code.
- No official evaluation, deployment, or paid calls. Formal strategy frozen.
- Preserve baseline caeeb2f and tag baseline/pre-coding-rsi-20261022.
- Refactor discussion: https://github.com/Hermetic-AI/hermetic-dmsa/issues/1.

## Task 1: Existing MemoryLayer correctness

Files: src/dmsa/memory/layer.py, focused mixins/helpers under memory/, src/dmsa/mcp_server/cli.py, tests/test_memory_correctness.py, docs/api-reference.md.

- [x] Add failing tests: configured SQLite survives reopen; deleted/expired nodes excluded without starving active results; learning rate zero rejects successor; explicit store still wins; missing parents remain typed errors.
- [x] Split oversized layer into focused modules preserving MemoryLayer public import, snapshots and serialization behavior.
- [x] Wire MemoryLayerConfig.storage_path into SQLiteStore, CLI passes config; use pathlib and typed errors.
- [x] Filter all candidates and lineage extensions for active/nonexpired nodes before ranking (do not only filter final k).
- [x] Run targeted tests, full suite and targeted lint/type; document known preexisting gates.

## Task 2: Scoped coding evidence engine

Files: src/dmsa/memory/coding/{models,extraction,storage,retrieval,engine}.py, tests/test_coding_memory.py, docs/algorithms/coding-memory.md.

Public contract: CodingMemory(path, embedder=None, policy=None); add(request_id, user_id, session_id, messages); search(query,user_id,top_k); delete_scope(user_id); close(). Messages validated through a shared SourceMessage type; search returns stable id/content/score records. Source spans and outcomes preserve uncertainty.

- [x] Test scope isolation, idempotent retries/conflicting payloads, restart, transactional failure, span preservation, symbols/errors, unknown outcome, negative examples, long content, top_k validation and deletion.
- [x] Store raw ordered messages and derived chunks in SQLite; request and revision committed atomically. Scope-local FTS candidate query; avoid cross-scope candidate starvation.
- [x] Extract paths/symbols/failure/validation snippets deterministically with exact source spans. Keep summaries conservative and searchable original evidence. Do not execute supplied code.
- [x] Fuse sparse/exact and optional dense candidates with stable RRF; bounded neighbor expansion; diversity and character/token budget. Dense initial backend is explicitly bounded reference implementation, not claimed full-scale ANN.
- [x] Embedder Protocol injection, fingerprint/dimension validation and no silent semantic-to-hash fallback.
- [x] Run meaningful tests and quality checks, update public docs.

## Task 3: Gated offline RSI policy

Files: src/dmsa/gradient/{policy,replay}.py, tests/test_policy_replay.py, docs/algorithms/offline-rsi.md.

Public contract: RetrievalPolicy is immutable/validated; ReplayCase carries scope/query/relevant evidence; ReplayReport records paired cases; PolicyRegistry persists candidates/accepted/rejected and active version, manifest hashes. Fixed gate refuses NaN/missing data/regression/overbudget; requires at least one improvement. Uses development evidence only, not runtime feedback.

- [x] Test normal promotion, no-change rejection, regressions, malformed/mismatched replay, budget and rollback, reload.
- [x] Implement bounded declarative policy, diagnostic-driven small proposals (exact quota, redundancy, expansion), deterministic paired retrieval replay, immutable reports and atomic active registry.
- [x] Keep proposer/score/gate fixed; expose explicit offline CLI/example. No fabricated end-to-end coding success claims or autonomous code execution.
- [x] Document that this release implements inner loop, outer meta-evolution remains research until independently validated.

## Task 4: HTTP integration, example and final validation

Files: src/dmsa/mcp_server/http/{contracts,app,cli}.py, pyproject.toml, tests/test_coding_http.py, examples/02_coding_memory.py, docs/USAGE.md, CHANGELOG.md.

- [x] Implement create_app(memory,api_key) with /health /add /search; require bearer key outside health; no key/content logging. Source messages support string and text parts. Reject incorrect types/empty IDs/options and top_k via typed 4xx.
- [x] Add optional http extra and dmsa-memory-http CLI; production flag enforces text-embedding-v4 and a real configured client, no silent hash fallback; offline sparse profile explicit.
- [x] Test ASGI calls, auth, errors, concurrency, scope, restarts, protocol envelopes, top_k and text parts.
- [ ] Run example without API key; full tests coverage >=80%; targeted strict lint/mypy; final review and fix important findings.
- [ ] Commit and push development branch, keep main baseline unchanged. Report actual functionality and scale limitations.

## Verification command

From hermetic-dmsa:

```powershell
uv run --no-project --with-editable ../analysis/pmd_skeleton --with pytest --with pytest-cov --with hypothesis --with ruff --with mypy --with fastapi --with httpx --with uvicorn --with openai python -m pytest -q
```

Targeted suites use `--no-cov` to avoid global coverage rejection for one component. Final suite keeps coverage gate. No API calls needed for tests.
