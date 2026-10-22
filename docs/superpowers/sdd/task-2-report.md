# Task 2 implementation report

Implemented the scoped coding evidence engine and exported its public API through `dmsa.memory`. The API provides `CodingMemory`, typed errors, immutable `SearchPolicy`, provenance-bearing `SearchHit`, and `CorpusState`. `add()` validates ordered `SourceMessage` inputs with numeric millisecond timestamps, performs payload-hash idempotency preflight before embedding, and rechecks inside the atomic SQLite write. Scope-local epochs persist across deletion.

SQLite WAL persists raw messages, deterministic source chunks with exact offsets, FTS rows, exact path/symbol terms, vectors with model fingerprint and dimension, request hashes, and revisions. Retrieval fuses exact, FTS, and optional dense candidates with stable RRF, performs bounded same-message neighbor expansion, and applies redundancy, per-session, character, and token budgets. Unknown outcomes remain unknown; code is never executed. Dense lookup is an explicitly bounded reference scan and raises when its configured scope limit is exceeded.

`corpus_state(user_ids)` returns the summed revision and a deterministic fingerprint over the sorted requested scopes, read in one SQLite transaction. Paired replay can capture this state before and after searches. Callers should only compare runs when both fingerprints match.

Verification on 2026-10-22:

- Focused coding-memory tests: 13 passed.
- Full suite: 219 passed.
- Ruff on new coding package and tests: passed.
- Strict mypy on coding package: passed.
- All new coding package Python files are at most 300 lines.

Dense retrieval remains O(scope size) and should be replaced by a production ANN backend for larger corpora.
