# Offline retrieval self-improvement

Integrated runtime: see [DMSA + RSI](../integrated-rsi.md). Explicit task feedback
now creates scoped replay cases. The service executes bounded proposals and
paired replay, then activates only passing candidates with a baseline/corpus
check. Unknown failure causes do not generate diagnoses. Persisted scope policies
are loaded on the next retrieval and support rollback.

This release implements only an inner development loop: repeated, labeled
retrieval failures can suggest small declarative changes to exact-channel
weight, redundancy threshold, or neighbor window. A redundancy diagnosis lowers
the threshold, which filters similar evidence more aggressively. The proposer requires at
least two failures and suppresses a change when successful counterexamples
outnumber failures. It cannot modify code or model weights.

`run_replay` invokes `CodingMemory.search` for baseline and candidate policies
against identical ordered cases, top-k, character/token budgets and a stable
multi-scope corpus fingerprint. It checks the corpus state before and after
both runs. Reports bind policy, case and corpus hashes. The fixed gate requires
nonempty paired evidence, identical IDs/slices/labels, no per-case recall
regression, and at least one strict per-case recall improvement. Recall is a
retrieval proxy; it does not establish coding success or agent pass rate.

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

The outer meta-evolution loop remains research until independently validated.
No official evaluation or competition score is claimed. Provider calls occur
only when the optional model advisor or configured SGA fusion is explicitly
invoked; there is no runtime feedback learning.

## Paper mapping

This module is an engineering realization of offline, evidence-gated retrieval
policy iteration around the coding-memory retriever. It complements the
provenance-grounded memory design in §3 and the evaluation methodology; it does
not claim a new paper result or validate an outer self-evolution loop.
