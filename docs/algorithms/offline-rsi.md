# Offline retrieval self-improvement

Integrated runtime: see [DMSA + RSI](../integrated-rsi.md). Explicit task feedback
now creates scoped replay cases. The service executes bounded proposals and
paired replay, then activates only passing candidates with a baseline/corpus
check. Unknown failure causes do not generate diagnoses. Persisted scope policies
are loaded on the next retrieval and support rollback.

This release implements only an inner development loop: repeated, labeled
retrieval failures can suggest small declarative changes to exact-channel
weight, redundancy threshold, neighbor window, code-anchor weight, or the
experience-card prior. A redundancy diagnosis lowers the threshold, which filters
similar evidence more aggressively. The `identifier_confusion` diagnosis raises
the code-anchor weight so structured anchors can outrank semantically similar
prose; the `stale_evidence` diagnosis lowers the experience-card prior because
derived cards are the most likely source of outdated evidence. The proposer requires
at least two failures and suppresses a change when successful counterexamples
outnumber failures. It cannot modify code or model weights.

`run_replay` invokes `CodingMemory.search` for baseline and candidate policies
against identical ordered cases, top-k, character/token budgets and a stable
multi-scope corpus fingerprint. It checks the corpus state before and after
both runs. Reports bind policy, case and corpus hashes. The fixed gate
(`offline-rsi-gate-v2`) requires nonempty paired evidence, identical IDs/slices/labels,
no per-case recall regression, no per-case NDCG regression, no token growth on cases
without a recall gain, and at least one strict improvement in recall or ordering
quality. Recall and NDCG are retrieval proxies; they do not establish coding
success or agent pass rate.

Retrieval traces now bind the corpus fingerprint, the policy digest, the repository
partition and an optional task identifier, so a later replay can be checked against the
corpus that produced the decision. Task outcomes are stored separately
(`rsi_task_results`) through `TaskResultStore` and `IntegratedRSI.record_task_result`:
callers report the verified outcome, and the system never infers a root cause from it.

Task outcomes now also constrain promotion through `dmsa.gradient.task_gate`.
`task_cases` joins traces, human/agent-confirmed relevant IDs and recorded outcomes;
only traces that have both a terminal outcome and confirmed evidence become cases, so
"returned but never confirmed useful" is never treated as a positive label. A candidate
that already passed the retrieval gate must additionally retain the confirmed evidence of
every previously successful task (`run_task_gate`); losing any of them rejects the candidate
and is audited as `task_gate_rejected`. When no task case exists yet the task gate is
vacuously satisfied (`checked=0`), so the retrieval gate remains the only real constraint —
this is recorded in the event payload rather than hidden.

Diagnoses are slice-aware: `dmsa.gradient.diagnosis.diagnose` aggregates failure labels
per failure kind but requires at least two failures across distinct slices
(`minimum_support`, `minimum_slices`) before a kind can drive a proposal, so a single noisy
session cannot reshape the policy. The evolvable policy space covers the retrieval channel
weights (exact/sparse/dense/experience/anchor), chunking (`chunk_size`, `chunk_overlap`),
stale-evidence penalty, skeleton depth and relation window; proposals remain bounded,
declarative values generated deterministically from diagnostics.

Replay cases carry the repository partition (`scope_key(user, repo)`) as their scope, and
activation re-reads the corpus fingerprint over exactly the partitions observed in those
cases; a corpus change anywhere in that set aborts activation.

`PolicyRegistry` stores candidate/accepted/rejected status, report evidence,
and the active version with SQLite transactions. Promotion is accepted only
for a report executed by `run_replay` in the current process; arbitrary metric
claims do not qualify. This is a local trust boundary, not cryptographic
attestation against a malicious in-process caller. A service never adopts a
candidate automatically. Development scripts must pass the explicit
`development_only=True` acknowledgement.

`ModelAssistedPolicyProposer` is an optional decision/text layer. A decision
model receives only aggregate supported failure labels/counts and bounded
deterministic candidates; it may return candidate indices but cannot invent
weights or directly mutate the registry. The text model writes a short
explanation from the selected policy only. Both outputs are advisory. Every
candidate still requires the unchanged `run_replay` + `gate` path before it can
be registered or promoted. The CLI is `dmsa-rsi-propose` and accepts a bounded
JSON payload on stdin; it does not call the evaluation provider unless a
candidate exists.

The outer loop is bounded and evidence-only: `dmsa.gradient.meta_evolution.compare_proposers`
answers "which proposer yields better candidates on this scope's own labeled cases", not
"which proposer should be switched on". It reads the persisted baseline, the slice-aware
diagnostics and the replay cases once, then gives the deterministic proposer and the
model-assisted proposer the same replay budget (`budget_replays // len(PROPOSERS)` replays
each, candidates truncated to that budget). Both arms run the unchanged `run_replay` +
`gate` path with `development_only=True`. Outcomes are recorded per arm
(`accepted` / `rejected` / `no_candidate` / `no_replay_case` / `unavailable` with the error
type, plus replay count and best mean recall/NDCG) and written as a single `meta_evolution`
audit event. The comparison activates nothing: `rsi_active` is untouched, so a human still
decides whether to flip the `models` switch. A winner is only named when at least one arm has
a gate-passing candidate, and ties go to the deterministic arm because equal evidence quality
should not justify model cost or an external dependency. Measured recall/NDCG here are
retrieval proxies on labeled cases; they are not coding success rates and no official
evaluation or competition score is claimed. Provider calls occur only when the optional model
advisor or configured SGA fusion is explicitly invoked; there is no runtime feedback learning.

## Paper mapping

This module is an engineering realization of offline, evidence-gated retrieval
policy iteration around the coding-memory retriever. It complements the
provenance-grounded memory design in §3 and the evaluation methodology; it does
not claim a new paper result or validate an outer self-evolution loop.
