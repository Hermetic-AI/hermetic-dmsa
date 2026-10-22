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
