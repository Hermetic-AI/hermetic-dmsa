# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Default-off lineage channels: same-session episode updates now mark the old
  card `superseded` instead of deleting it, record `DERIVED_FROM` /
  `SUPERSEDED_BY` edges, and expose `[supersedes]` / `[superseded]` markers
  through `SearchPolicy.lineage_window`.
- Default-off anchor-scoped verification prior (`verification_weight`): only
  explicit test anchors whose paths overlap the evidence paths affect ranking.
- `superseded_evidence` and `overclaimed_verification` diagnoses now generate
  bounded RSI policy proposals.

## [0.2.0] — Unreleased

The 0.2.0 line introduces the Coding Memory engine: a provenance-aware store for
coding-agent history that can be searched over HTTP or through MCP, plus an
offline RSI loop that proposes and gates retrieval-policy changes.

### Added

- Coding Memory engine: SQLite-backed persistence of raw messages and slices with
  request/session/message provenance and character offsets.
- Hybrid retrieval: exact path/symbol matching, FTS5 full-text search, and an
  optional dense-embedding channel fused through RRF, with de-duplication,
  per-session limits, and an explicit delivery budget.
- Optional HTTP API and `dmsa-memory-http` CLI, with `/add`, `/search`,
  `/feedback`, `/task_result`, and the `/rsi/*` proposal, evolution, and
  rollback endpoints.
- Optional dense mode: DashScope native embedding endpoints for
  `qwen3.7-text-embedding-flash` and the compatible `text-embedding-v4` alias.
- Offline RSI: the advisor may only select among rule-generated candidates, and
  every accepted policy change must pass a fixed paired-replay gate that blocks
  upgrades on any retrieval regression.
- MCP server exposing 14 `dmsa_coding_memory_*` tools over stdio, with a remote
  mode that forwards to the HTTP backend.
- Observability: authenticated `GET /metrics` and `GET /status` endpoints, a
  trace ID that reuses upstream `X-Request-ID`/`traceparent` headers, and latency
  instrumentation for the embedding, vector-index, search, and HTTP paths.
- Structured logging on by default in every service and CLI entry point, driven
  by `DMSA_LOG_LEVEL` / `DMSA_LOG_FORMAT` / `DMSA_LOG_COLOR`.
- Standalone distribution: bundled PMD source archive with recorded SHA-256,
  hash-pinned Docker dependency lock, non-root read-only image, persistent
  Compose deployment, and mounted secrets.
- Read-only memory browser (`dmsa-memory-browser`) and the `dmsa-inspect`
  diagnostic CLI.
- Documentation site, benchmark harness for the coding-memory evaluation, and
  agent integrations for Claude Code, OpenCode, Codex, and Hermes.

### Changed

- Logging is now on by default; stdio MCP keeps stdout reserved for JSON-RPC and
  writes all logs to stderr.
- Latency metrics moved to dedicated `COUNT_BUCKETS`, because the previous
  latency buckets collapsed every value of 10 or more into `+Inf` and carried no
  information.
- `Counter.inc()` on a negative amount now raises `MetricValueError`
  (`DMSA-0003`).
- Remote-mode `dmsa_coding_memory_status` now calls `/status` instead of
  `/health`, so it reports whether embedding, the vector index, and RSI are
  actually active.
- The competition profile now requires an explicit embedding model and fails
  before storage or server initialization when it is missing.

### Fixed

- The embedding adapter dispatches by model family, so native endpoints are no
  longer given a `/embeddings` suffix.
- Both Qwen embedding aliases are accepted, so a valid deployment is no longer
  rejected at startup.
- The flash embedding response shape (an extra `index` field) is accepted.
- Embedding requests back off and retry on rate limits, and an outbound
  concurrency gate bounds provider load.
- When the dense channel is unavailable, writes and searches degrade to sparse
  retrieval instead of failing.
- Persistent vectors are stored as `float32` bytes.
- Write-side vector reuse skips embedding calls for unchanged message bodies.
- The evidence delivery ceiling is no longer suppressed by the scoring pass, so
  channel ranking decides the order.

### Fixed

- Python 3.10 no longer fails at import time: `logging.StreamHandler` only became
  subscriptable in 3.11, so the handler base class now resolves through a
  type-checking alias instead of a runtime subscript.
- The stdio MCP server pins its protocol streams to UTF-8. Tool descriptions are
  Chinese, and on a non-UTF-8 locale (for example `cp1252` on Windows) the server
  previously died while writing a `tools/list` response.
- The coding-memory agent adapter sends and receives UTF-8 explicitly, so a
  payload containing non-ASCII text is no longer corrupted or rejected by the
  platform default encoding.
- The retrieval score no longer drifts with the wall clock. Trust decay is
  quantised to whole days — the half-life is 30 days, so second-level jitter was
  meaningless noise that made the same query return a different score across a
  second boundary, and left the replay gate unable to tell a policy change from
  clock drift.
- The Docker container contract test supplies its own throwaway API key.
  `compose.yaml` declares `DMSA_MEMORY_API_KEY` as required, and the test used to
  strip every `DMSA_*` variable, so `docker compose` aborted during interpolation.
- The Compose secrets deployment documented in `docs/deployment.md` now works as
  written. The base Compose file no longer requires the API key at interpolation
  time — that check aborted every multi-overlay invocation, including the one
  that deliberately keeps the key out of the environment. A missing key is now
  rejected by the startup validation, which exits the container with
  `required environment variable is unset: DMSA_MEMORY_API_KEY`.
- The competition Compose overlay no longer fills in a default embedding model.
  The competition profile requires an explicit model, exactly as the deployment
  guide states.
- `opentelemetry-api` is pinned to 1.45.2, clearing CVE-2026-126360.
- The bundled PMD archive and the pinned dependency lock agree on the archive's
  SHA-256 again.

### Known Limitations

- The dense channel is a bounded in-scope scan (10,000 slices by default), not a
  large-scale ANN index.
- HTTP accepts text only; `options` must be an empty array. `/v1/runs` and a
  new-version MCP bridge are not implemented.
- The API key authorizes the whole service; `user_id` is a retrieval partition,
  not an independent authentication mechanism.
- Provider compatibility and evaluation scale remain unvalidated.

## [0.1.0]

### Added

- Initial project scaffolding.
- L1 memory layer wrapping the PMD skeleton.
- L2 SGA textual-gradient aggregation with a zero-dependency fallback.
- L3 ToolBP schema projection for tool-use backpropagation.
- L4 MCP server exposing 7 tools to agents.
- L5 integrations for Claude Code, OpenCode, and the Hermes skill.
- `dmsa-inspect` read-only diagnostic CLI with 5 subcommands.
- Runnable examples and full CI/CD (ruff, strict mypy, pytest, multi-platform).
- Real LLM backend: `OpenAIEmbedder`, `LLMFusion`, and a `build_embedder` factory.
- `MemoryLayer.attach_embedder` and `MemoryLayer.apply_text_gradient` public API.
- Benchmark framework under `benchmarks/` for the SGA and ToolBP comparisons.

### Fixed

- MCP server could not run over stdio on Windows; `run_stdio` now uses a
  background thread and an `asyncio.Queue` so it works with the Proactor event
  loop.
- `dmsa_search` failed with `requires an embedder`; the server now attaches a
  `HashEmbedder` to layers without one.
- `dmsa_apply_gradient` was a stub and now writes the fused textual gradient
  back into the memory layer with lineage, supporting snapshot and rollback.

[Unreleased]: https://github.com/Hermetic-AI/hermetic-dmsa/compare/v0.1.0...HEAD
[0.2.0]: https://github.com/Hermetic-AI/hermetic-dmsa/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Hermetic-AI/hermetic-dmsa/releases/tag/v0.1.0
