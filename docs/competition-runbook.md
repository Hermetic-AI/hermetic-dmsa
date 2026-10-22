# Competition runbook

This runbook freezes evaluation inputs and distinguishes local engineering
self-tests from the official 150-task score.

## Before an evaluation

1. Obtain the competition's authorized task manifest, rules, model allowlist,
   noise-generation procedure, official runner, and submission URL. Do not
   infer or synthesize these from the 150-task description.
2. Confirm task data may be stored and transmitted to the configured embedding
   and agent providers. Store credentials outside the repository.
3. Pin a clean repository commit, Python/container image, package lock, model
   IDs, embedding model/dimension, prompt, agent tool policy, memory database
   snapshot, and benchmark seed.
4. Run unit/integration tests, offline synthetic proxy evaluation, service
   smoke, restart persistence, deletion, and scope-isolation tests.
5. Use separate clean worktrees for paired no-memory/sparse/hybrid/full runs.
   Reset each to the same base commit and test environment. The agent adapter
   must record actual test command and exit code; never accept memory's prior
   `reported_success` as present-run verification.
6. Freeze retrieval weights and corpus before the official run. Do not tune on
   official test answers or let task results enter memory during paired runs.
7. Run the official platform Smoke first. Run Full only after that exact
   endpoint/model/config is accepted by the platform.

## What to record

Persist one immutable run artifact containing:

- task manifest SHA-256 and permission/license provenance;
- code commit, image digest, Python, dependency lock hash, benchmark seed;
- model/provider identifiers and endpoint hash (never API key), embedding model
  and dimensions, retrieval policy, agent prompt/tool configuration hash;
- per task/arm/noise level: selected evidence IDs, actioned IDs, localization,
  change summary, test command + exit code, resolved status, tool calls, tokens,
  latency, known cost, and failure reason;
- raw official runner response, official score, and platform run ID, if supplied.

Missing test evidence, non-finite metrics, mismatched task pairs, changed corpus,
or exceeded budget invalidates a comparison rather than becoming a zero or a
success. Report unknown provider cost as unknown, not `$0`.

## Local proxy command

```powershell
.venv/Scripts/python.exe -m benchmarks.coding `
  --manifest benchmarks/coding/fixtures/mini.json `
  --arms no_memory sparse --seed 17 `
  --output benchmarks/results/coding-memory-mini.json
```

The checked-in fixture is synthetic and only checks pipeline/retrieval
regressions. It is not drawn from the official 150 tasks. See
[`coding/README.md`](coding/README.md) for an isolated agent-wrapper contract
and optional paid-provider switches. The direct chat adapter reports model
claims; only an external coding-agent wrapper plus independently executed
tests provides a meaningful software-engineering outcome.

## External gates

Real competition score requires the protected 150-task inputs/runner, allowed
model credentials, and platform endpoint/AML key. The repo's local proxy score
must never be substituted for any of these. If an external gate is missing,
save the local artifact and state exactly which gate prevented official
execution.
