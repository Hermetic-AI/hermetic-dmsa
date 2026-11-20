<div align="center">

# hermetic-dmsa

**A provenance-aware memory layer for coding agents — store what happened, retrieve what applies, and improve the retrieval policy offline.**

[![CI](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/ci.yml/badge.svg)](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/ci.yml)
[![Docs](https://github.com/Hermetic-AI/hermetic-dmsa/actions/workflows/docs.yml/badge.svg)](https://hermetic-ai.github.io/hermetic-dmsa/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Typing: mypy strict](https://img.shields.io/badge/typing-mypy%20strict-2a6db2.svg)](https://mypy-lang.org/)

[English](README.md) | [简体中文](README.zh-CN.md)

</div>

---

`hermetic-dmsa` gives a coding agent a durable memory of the repository it works on:
what broke, where it was, why it was fixed that way, and how the fix was verified.
It stores the raw history, returns traceable evidence under an explicit budget, and
can propose and gate improvements to its own retrieval policy — offline, on a
development set, with every accepted change replayed against a frozen corpus.

It is a library, an MCP server, and an HTTP service. It does not edit code, run
tests, or judge patches.

## Why it exists

Retrieval-augmented coding agents fail in a specific way: the historical evidence
they retrieve is *similar* but not *applicable*. A patch that fixed a stale version
of a function looks perfect for today's task and breaks the build. A failure log
mentions the right symbol but the wrong root cause. Long raw logs crowd out the one
line that mattered.

`hermetic-dmsa` is built around that problem:

| Capability | What it does |
|---|---|
| **Stores the original** | Raw messages, roles, timestamps, and character offsets are persisted verbatim and stay the final source of truth. |
| **Finds the applicable** | Exact path/symbol matching, FTS5 full-text search, and an optional dense channel are fused through RRF, then filtered by scope, condition, and version. |
| **Shows its sources** | Every returned chunk carries a provenance header; experience cards are deterministic views over source text, never invented summaries. |
| **Admits uncertainty** | A "reported success" stays a reported success. Test evidence proves only that test, not the patch. |
| **Improves offline** | A retrieval-policy change is proposed, replayed against paired runs, and accepted only if a frozen replay gate finds no regression. |

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│ L5  Integrations — Claude Code · OpenCode · Codex · Hermes      │
└──────────────────────────────────────────────────────────────────┘
                                │ stdio JSON-RPC 2.0  /  HTTP
┌──────────────────────────────────────────────────────────────────┐
│ L4  MCP Server (dmsa.mcp_server)                                │
│     7 legacy tools + 14 dmsa_coding_memory_* tools               │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L3  Tool-Use Backpropagation (dmsa.toolbp)                      │
│     JSON Schema AST projection + validated edit operations       │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L2  Semantic Gradient Aggregation (dmsa.gradient)               │
│     SGA clustering · policy evolution · paired replay · gate     │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L1  Memory Layer (dmsa.memory)                                  │
│     Coding Memory engine + PMD provenance DAG                    │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ L0  pmd — bundled, hash-pinned source snapshot                  │
└──────────────────────────────────────────────────────────────────┘
```

Layers may only import downwards, and cross-layer access always goes through a
layer's public `__init__` API. See [docs/architecture.md](docs/architecture.md).

## Quick start

### Docker (no API keys required)

The `sparse-dev` profile runs entirely offline — no LLM, no embedding provider.

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
docker compose up -d --build
docker compose exec -T memory dmsa-memory-smoke
```

The memory API listens on `127.0.0.1:8000`; the read-only memory browser listens on
`127.0.0.1:8765`. Data persists in the `memory-data` volume.

```bash
export DMSA_MEMORY_API_KEY=dev-secret

curl -sS http://127.0.0.1:8000/health

curl -sS http://127.0.0.1:8000/add \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"demo-1","user_id":"demo-repo","session_id":"agent-a",
       "messages":[{"role":"user","content":"load_cache returns stale values under concurrency"},
                   {"role":"assistant","content":"Check the version before replacing the cache."},
                   {"role":"tool","content":"pytest tests/test_cache.py: 3 passed"}]}'

curl -sS http://127.0.0.1:8000/search \
  -H "Authorization: Bearer $DMSA_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"load_cache stale value","user_id":"demo-repo","top_k":5,"options":[]}'
```

### From source

Requires Python 3.10+ and a SQLite build with FTS5. The pinned PMD source archive
ships in the repository — no network fetch of an unverifiable package.

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install ./vendor/pmd-0.1.0.tar.gz
pip install -e ".[http,dev]" httpx

export DMSA_MEMORY_API_KEY=dev-secret
dmsa-memory-http --profile sparse-dev --storage ./coding.sqlite3
```

Full walkthrough: [docs/quickstart.md](docs/quickstart.md).

## Using it from an agent

The HTTP service is the primary integration surface:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check, no authentication |
| `POST` | `/add` | Persist one request's ordered messages (idempotent) |
| `POST` | `/search` | Retrieve evidence for a scope |
| `POST` | `/feedback` | Record retrieval feedback against a `trace_id` |
| `POST` | `/task_result` | Report task outcome facts (never triggers evolution) |
| `GET` | `/status` | Whether embedding, the vector index, and RSI are actually active |
| `GET` | `/metrics` | Per-process latency, delivery count, and status counters |
| `POST` | `/v1/memories/rsi/*` | Propose, evolve, and roll back retrieval policies |

The MCP server exposes the same capability over stdio as 14
`dmsa_coding_memory_*` tools, and can forward to a remote HTTP backend. Agent
integration guides live in [integrations/](integrations/) and
[docs/integrations.md](docs/integrations.md).

> **Retrieval evidence is untrusted input.** Stored history can be stale, wrong, or
> carry prompt injection. Treat every retrieved chunk as data to be checked against
> the current code and tests — never as an instruction to execute.

## Offline RSI

The retrieval policy can change, but only under a gate:

1. A failure on the development set is diagnosed into a typed category.
2. The semantic-gradient aggregator clusters comparable failures and proposes a
   small, declarative candidate.
3. Baseline and candidate are replayed as a paired run over a frozen corpus.
4. The gate rejects NaN metrics, missing measurements, budget overruns, or any
   observed regression on protected tasks.
5. An accepted candidate becomes an activatable version that can be rolled back.

The advisor may only select among rule-generated candidates; it never writes policy
code. See [docs/algorithms/offline-rsi.md](docs/algorithms/offline-rsi.md).

## Benchmarks

`benchmarks/coding/` provides a reproducible harness with a synthetic proxy fixture,
multi-arm comparison, ablations, and a self-generated task loop. The checked-in
fixture is synthetic: it detects pipeline and retrieval regressions, and is not a
substitute for an official evaluation. Methodology is in
[docs/evaluation-runbook.md](docs/evaluation-runbook.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | Five-minute path from clone to first retrieved memory |
| [docs/coding-memory-guide.md](docs/coding-memory-guide.md) | Full technical guide: startup, HTTP contract, agent integration, paired experiments |
| [docs/design/coding-memory.md](docs/design/coding-memory.md) | Design rationale: memory representation, Add/Search, RSI constraints |
| [docs/api-reference.md](docs/api-reference.md) | Public API surface |
| [docs/algorithms/](docs/algorithms/) | Retrieval, extensions, and offline RSI algorithms |
| [docs/deployment.md](docs/deployment.md) | Docker, secrets, production topology, standalone distribution |
| [docs/security-and-data.md](docs/security-and-data.md) | Data boundaries, retention, and trust model |
| [docs/USAGE.md](docs/USAGE.md) | Long-form user manual |
| [docs/paper-mapping.md](docs/paper-mapping.md) | Algorithm-to-literature mapping |

## Development

```bash
make install      # project + pinned PMD archive
make lint         # ruff check + format --check
make type         # mypy --strict
make cov          # pytest with the 80% coverage gate
make ci           # all of the above
```

CI runs ruff, strict mypy, and pytest across Linux, macOS, and Windows on Python
3.10–3.12, plus a coverage gate, a dependency audit, a wheel/sdist build, and
standalone container contract tests.

- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution workflow and code conventions
- [AGENTS.md](AGENTS.md) — conventions for AI coding agents working in this repo
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — expected behaviour
- [SECURITY.md](SECURITY.md) — reporting a vulnerability
- [CHANGELOG.md](CHANGELOG.md) — release history

## Status and limitations

This is a `0.2.0.dev0` development build. Stated plainly:

- The dense channel is a **bounded in-scope scan** (10,000 chunks by default), not a
  large-scale ANN index.
- The HTTP API accepts text only; `options` must be an empty array. `/v1/runs` and a
  new-generation MCP bridge are not implemented.
- A single API key authorizes the whole service. `user_id` is a retrieval
  partition, **not** a per-user authentication mechanism.
- Dense mode sends source text to the configured embedding provider. The `sparse-dev`
  profile sends nothing off the machine.
- Provider compatibility and evaluation scale at production volume are unvalidated.
- The legacy PMD DAG, SGA textual gradients, and ToolBP remain available but do not
  yet share memory with the Coding Memory engine automatically.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). The bundled PMD source snapshot
is attributed to PMD Maintainers; see [vendor/README.md](vendor/README.md) for
provenance and integrity records.
