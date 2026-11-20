# hermetic-dmsa

**A provenance-aware memory layer for coding agents — store what happened, retrieve what applies, and improve the retrieval policy offline.**

`hermetic-dmsa` gives a coding agent a durable memory of the repository it works
on: what broke, where it was, why it was fixed that way, and how the fix was
verified. It stores the raw history, returns traceable evidence under an explicit
budget, and can propose and gate improvements to its own retrieval policy —
offline, on a development set, with every accepted change replayed against a
frozen corpus.

It is a library, an MCP server, and an HTTP service. It does not edit code, run
tests, or judge patches.

## Where to start

| If you want to | Go to |
|---|---|
| Run it in five minutes | [Quick start](quickstart.md) |
| Understand the layers | [Architecture](architecture.md) |
| Wire it into an agent | [Technical guide](coding-memory-guide.md) |
| Call the HTTP API | [HTTP API reference](api-reference.md) |
| Understand the design | [Design](design/coding-memory.md) |
| Read the algorithms | [Algorithms](algorithms/coding-memory.md) |
| Deploy it for real | [Deployment](deployment.md) |
| Know what it will not do | [Status and limitations](#status-and-limitations) |

## Quick start

```bash
git clone https://github.com/Hermetic-AI/hermetic-dmsa.git
cd hermetic-dmsa
docker compose up -d --build
docker compose exec -T memory dmsa-memory-smoke
```

The `sparse-dev` profile runs entirely offline — no LLM, no embedding provider.
The memory API listens on `127.0.0.1:8000`; the read-only memory browser listens
on `127.0.0.1:8765`.

## Status and limitations

This is a `0.2.0.dev0` development build.

- The dense channel is a **bounded in-scope scan** (10,000 chunks by default), not a
  large-scale ANN index.
- The HTTP API accepts text only; `options` must be an empty array. `/v1/runs` and a
  new-generation MCP bridge are not implemented.
- A single API key authorizes the whole service. `user_id` is a retrieval
  partition, **not** a per-user authentication mechanism.
- Dense mode sends source text to the configured embedding provider. The
  `sparse-dev` profile sends nothing off the machine.
- Provider compatibility and evaluation scale at production volume are unvalidated.

## License

Apache License 2.0. See the
[repository](https://github.com/Hermetic-AI/hermetic-dmsa) for `LICENSE`,
`THIRD_PARTY_LICENSES.md`, and the bundled PMD provenance records.
