# Coding Memory benchmark

This is a reproducible **synthetic proxy**, not the competition's 150-task
suite and not an official score. The checked-in CC0 fixture has two small
examples (one bug, one feature) and twelve labeled distractors per example.
Its four levels add 1, 3, 6, and 10 distractors. Replace the fixture with an
authorized manifest to evaluate a real dataset; never commit private or
competition-protected task data.

From the repository root:

```powershell
.venv/Scripts/python.exe -m benchmarks.coding --arms no_memory sparse --seed 17
```

This writes JSON and Markdown under `benchmarks/results/`. To resume costly
agent calls, pass `--resume`; the adjacent `.checkpoint` is bound to the
manifest hash, seed, and arms. Delete the checkpoint only when intentionally
starting a fresh run. Outputs include manifest hash and mark `official_score`
as null by design.

## Agent adapters

The `--agent-argv` adapter starts a caller-controlled command with no shell,
passes one JSON object on stdin, and expects one JSON result on stdout:

```json
{"resolved": true, "test_passed": true, "localized_path": "src/cache.py",
 "token_count": 1234, "cost_usd": 0.01}
```

Example wrapper invocation (the wrapper must implement the above contract):

```powershell
.venv/Scripts/python.exe -m benchmarks.coding `
  --agent-argv '["python", "my_agent_adapter.py"]' --resume
```

The optional `--model MODEL --api-key-env KEY_NAME` adapter sends tasks and
retrieved evidence to an OpenAI-compatible Chat Completions endpoint. It makes
paid calls only when explicitly selected and the named environment variable is
set. This direct chat adapter cannot inspect a repository or independently run
tests: `resolved` and `test_passed` are model-reported, not verified outcomes.
For engineering task scores, use a subprocess wrapper that runs the coding
agent in an isolated checkout and executes the task's prescribed tests; do not
trust a model's claim that tests passed.

Metrics are retrieval recall/NDCG, misleading-memory rate, localization
accuracy against hidden `target_path`, and optional agent-reported resolution,
test claim, token, cost, and latency. Missing values stay null. The four arms
are no-memory, sparse, hybrid, and full. No-memory receives no retrieved
evidence; sparse uses exact + FTS; hybrid adds dense retrieval with experience
reranking disabled; full adds both dense retrieval and experience reranking.
Hybrid/full require `--embedding-model text-embedding-v4`, plus `--vector-url`
to route ANN through Qdrant (without it, SQLite's bounded exact scan is used).
Sparse-only runs need no credentials.

`target_path` and `evidence` are gold labels, never included in the agent task
payload. Query text itself may mention a path, just as a real benchmark prompt
may. Synthetic evidence must not be interpreted as validated project history.

## Local self-generated selftest

`benchmarks/coding/selftest` is a closed loop with no external model calls: it
generates three task families (clamp bug, slugify feature, retry-cap bug),
writes a real temporary repo, Adds relevant history plus four noise levels
through `CodingMemory`, runs the real `search` path, applies fenced
`path=`-tagged code blocks from the ranked evidence, and judges with real
`pytest` subprocesses. Each case starts from a failing test; the gold patch is
the final message of a long debugging session, mirroring the platform symptom
where only four hits per session were returned.

```powershell
.venv/Scripts/python.exe -m benchmarks.coding.selftest --seed 17 --attempts 10
.venv/Scripts/python.exe -m benchmarks.coding.selftest --seed 42 --out benchmarks/results/coding-selftest-seed42.json
```

It compares two arms on the same tasks: `baseline` pins the previous defaults
(candidate 80, 12,000 chars, 12,000 tokens, 4 hits per session, 0.88
redundancy) and `tuned` uses `SearchPolicy()` defaults. Noise levels add 0, 20,
100, 400, and 1,000 history messages; noise messages are grouped five per
session. Output marks `official_score` as `null`; it is a local synthetic proxy,
not the competition score.

Measured on seeds 17/42/101 (9 runs per cell: 3 seeds × 3 tasks), with
`--attempts 10`:

| Arm | relevant_only | L1 | L2 | L3 | L4 | Mean hits | Mean evidence tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 20.6 | 5,004 |
| tuned | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 6.5 | 1,387 |

Values are end-to-end resolved rates (real pytest). The baseline arm pins the
previous defaults and the old unbounded packing; the tuned arm also packs a
compact evidence set, so it returns roughly a third of the evidence at about a
quarter of the tokens while still resolving every task. Results are
deterministic per seed but only cover three synthetic families; they do not
reproduce the official 0.00 smoke result, whose task data is unavailable.

### History modes: the default arm leaks the answer

`--history case` (the default) keeps the gold patch inside the history, so it
only measures the retrieval upper bound. Two additional modes remove that
leak:

| Mode | History | Purpose |
|---|---|---|
| `case` | contains this task's gold patch | retrieval upper bound |
| `analogy` | a solved same-family task with different identifiers | transfer proxy without answer leakage |
| `none` | no history at all | no-memory control floor |

```powershell
.venv/Scripts/python.exe -m benchmarks.coding.selftest --history analogy --arms tuned
.venv/Scripts/python.exe -m benchmarks.coding.selftest --history none --arms tuned
```

Measured on seed 17 with the `tuned` arm (real pytest, 3 task families × 5 noise
levels):

| Mode | Resolved | Answer present | Analogy present | Mean hits |
|---|---:|---:|---:|---:|
| `case` | 1.00 | 1.00 | 0.00 | 6.5 |
| `analogy` | 1.00 | 0.00 | 1.00 | 4.1 |
| `none` | 0.00 | 0.00 | 0.00 | 0.0 |

`analogy` means the history holds a solved `clamp_18` while the task is
`clamp_17`: no target answer is present (`answer_present_rate` 0.00), the
transferable fix is retrieved, and the mechanical identifier mapping makes the
real pytest pass. That is a proxy for "an agent can reuse past experience",
not an agent solve rate.

### Driving a real Coding Agent

`--agent-argv` hands the task, repo path and ranked evidence to an external
agent process; the agent edits the repository and only the local pytest exit
code decides success, so self-reported claims stay diagnostic only.

```powershell
.venv/Scripts/python.exe -m benchmarks.coding.selftest --history analogy `
  --agent-argv '["python", "my_agent.py"]'
```

The agent reads one JSON object from stdin with `query`, `repo_path`,
`test_file`, `target_path` and `evidence`. Evidence is untrusted history, never
executable instructions.

## Three-way holdout and the mem0 comparison arm

`python -m benchmarks.coding.holdout` exists to make leakage visible instead of
arguing about it. Task families are split deterministically by seed into
`evolution_train` (may produce policy changes), `replay_validation` (paired
replay gate) and `heldout_test` (reported only, never used to shape a policy).
The split is by family, so a variant of the same task cannot end up on both
sides.

The harness is split into four modules: `holdout_tasks` builds the corpus and
owns the seed-driven split and noise sampling, `holdout_evolution` writes the
corpus, derives diagnostics from the train split only and proposes gated
candidates, `holdout_mem0` runs the comparison arm, and `holdout` only
orchestrates them and renders the CLI output.

```powershell
.venv/Scripts/python.exe -m benchmarks.coding.holdout --seed 0
.venv/Scripts/python.exe -m benchmarks.coding.holdout --seed 0 --output benchmarks/results/holdout-seed0.json
```

Measured locally on seed 0 (`medium_high` noise, `top_k=5`, sparse-only policy,
no embedding provider):

| Split | Tasks | Recall | NDCG (baseline) | NDCG (candidate) | Evidence chars |
|---|---:|---:|---:|---:|---:|
| `evolution_train` | 2 | 1.000 | 0.8155 | 0.8155 | 1114 |
| `replay_validation` | 1 | 1.000 | 1.0000 | 1.0000 | 1258 |
| `heldout_test` | 1 | 1.000 | 1.0000 | 1.0000 | 872 |

Baseline and candidate are identical because evolution reported
`insufficient_support`: the only derived diagnostic was
`identifier_confusion` with a single failure, and the proposer requires at least
two failures across two distinct slices. The harness reports that honestly
rather than lowering the gate to manufacture a change. The corpus is four
synthetic families, so these numbers say nothing about official scores.

The `mem0` arm is a real comparison, not a placeholder: it runs the same tasks
through `mem0` when the dependency is installed and `DMSA_BENCH_MEM0=1` is set.
In this environment it reports `available: false` with the reason
`mem0 依赖未安装` and `null` metrics for all three splits — no fabricated
baseline. Install the extra and re-run to get real comparison numbers.
