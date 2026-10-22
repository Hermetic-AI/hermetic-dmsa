# Final fixes report

## Findings and changes

- HTTP text-part validation now raises `CodingMemoryValidationError`; unsupported image parts return sanitized 422. `compare_digest` already receives UTF-8 bytes, so the alleged non-ASCII comparison crash was unsupported. Tests cover a Unicode-configured key mismatch and generic provider 503 behavior.
- `CodingRetriever.embed_new` wraps provider exceptions as `CodingMemoryError`; HTTP translates that to a sanitized 503. It does not fall back to hash vectors. The alleged silent competition-profile embedding fallback was unsupported by the implementation.
- Exact terms are lowercased before set deduplication, preventing `Foo` / `foo` primary-key collisions. Success extraction excludes explicit negation such as “tests not passed.”
- SQLite initialization closes its connection on setup failure. HTTP embedding responses now require a complete, unique integer index permutation before vector ordering.
- SQLite rollback now raises `MemoryLayerError` because PMD rollback changes only the live DAG. Detached export/import remains usable and restores explicit exported lineage edges in parent-first order, including after SQLite reopen.
- Corpus fingerprints now include persistent database identity, global corpus generation, and selected scope epochs. This distinguishes equal-revision databases and invalidates replay when another scope changes FTS global ranking statistics. Writers serialize revision assignment with `BEGIN IMMEDIATE`; idempotent retries leave generation unchanged.
- Stable-ID inputs reject NUL delimiters. Existing validation diagnostics name the individual config field, including `mcp_server.max_feedback_length`.
- Documentation fixes the shell API-key example and stale HTTP profile descriptions.

## Validation

- Focused tests: 62 passed (`test_config`, HTTP embedding, coding-memory, coding-HTTP, and memory correctness suites).
- Ruff: all changed implementation and test files passed.
- Mypy: all changed implementation files passed.
- `git diff --check`: passed.
- The parent agent ran the full test suite after these behavior changes: 265 passed, 85.35% coverage.

## Remaining limitations

- SQLite rollback is explicitly unsupported until the upstream PMD store can perform durable rollback.
- Competition endpoint compatibility and large-scale deployment remain unverified; provider calls can fail and return sanitized errors.
