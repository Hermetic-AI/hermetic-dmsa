# Security, threat model, and data handling

This document describes the supported single-operator deployment boundary. It
does not claim a multi-tenant security boundary or independent certification.

## Trust boundaries

| Asset / boundary | Threat | Current control | Operator responsibility |
|---|---|---|---|
| HTTP service | Unauthorized Add/Search | Bearer service key; malformed requests rejected | Use a long random key; rotate it; terminate public TLS upstream |
| `user_id` scope | Caller reads another scope | SQL and Qdrant filter every retrieval by exact scope | `user_id` is caller-supplied, not an authenticated principal; isolate clients or add an authenticated gateway before shared public use |
| Historical messages | Prompt injection, stale API advice, secrets | Raw evidence is labeled with provenance; experience cards only extract explicit text; no execution | Treat returned memory as untrusted data; review retention and redact before Add |
| Embedding provider | Source text disclosure / credential misuse | Explicit competition profile; endpoint and model validation; key from environment/file | Review provider terms, data residency, endpoint ownership, and key scope |
| SQLite / Qdrant volumes | Theft, accidental exposure, stale deletion | Persistent Docker volumes; Qdrant has no published host port in Compose | Restrict host access, encrypt disks/backups, stop service before file-level copy, set retention/deletion policy |
| Container / dependencies | Vulnerable transitive package or compromised image | Pinned local PMD archive, dependency lock, pip-audit CI, non-root/read-only app container | Review CI and upstream advisories; rebuild on security updates; scan the resolved image in release CI |

## Explicit limitations

- One API key authorizes access to every `user_id` served by that process.
  Scope IDs are a data partition, not authorization. Do not expose the default
  service directly to unrelated tenants.
- Local SQLite and Qdrant data are not application-encrypted. Protect the host,
  Docker volumes, swap, backups, and crash dumps. Use encrypted storage where
  policy requires it.
- The Add API stores supplied messages as source evidence. It cannot infer or
  remove API keys, personal data, or proprietary code reliably.
- Memory content can contain adversarial instructions. A Coding Agent must
  treat retrieved items as quoted historical evidence, never as system policy
  or executable commands; current code/tests and task instructions take priority.
- Qdrant is internal to the Compose network by default, but host administrators
  and Docker network peers remain trusted. Do not publish its port without an
  authenticated, TLS-protected access design.
- Embedding requests send chunk text to the configured provider. Sparse-dev
  avoids this egress; competition mode does not.

## Retention and deletion

Choose a retention period before collecting real work. Keep only evidence
needed for the declared evaluation, use separate databases/scopes per project,
and do not ingest secrets or data you lack permission to process. `CodingMemory`
supports `delete_scope(user_id)` and removes matching SQLite and configured
vector rows. The HTTP service intentionally has no unauthenticated/admin delete
route; perform deletion through a trusted operator process, verify both stores,
then expire backups separately. Deleting a named Docker volume is irreversible
unless a backup exists.

For a consistent SQLite backup, stop the service or use SQLite's backup API so
the WAL is included. To retire all local data, stop Compose and explicitly
remove the named volumes only after confirming the exact Compose project and
backup requirements.

## Incident response checklist

1. Restrict the service network and rotate the bearer/provider credentials.
2. Preserve sanitized logs and image/dependency identifiers; do not copy source
   payloads into an incident ticket.
3. Identify affected `user_id` scopes and provider requests from request IDs.
4. Delete affected data from SQLite and Qdrant, then address backup retention.
5. Rebuild from a reviewed commit/lock, run smoke and scope-isolation checks,
   and document residual exposure.
