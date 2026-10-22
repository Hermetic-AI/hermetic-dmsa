# Release checklist

- [ ] Confirm Apache-2.0 headers/license and third-party notices for all new
  source, fixtures, models, and vendored components.
- [ ] Read all applicable `AGENTS.md` files; keep upstream reference projects
  untouched.
- [ ] Run tests with coverage, Ruff check/format, strict mypy, package build,
  and dependency audit from the documented checkout.
- [ ] Build the image from a clean checkout; verify non-root, read-only rootfs,
  health/smoke, persistent restart, no public Qdrant port, and volume deletion
  instructions.
- [ ] Verify `.env.example` contains placeholders only; rotate any accidentally
  exposed credential and purge it from history before publishing.
- [ ] Generate the offline benchmark artifact and inspect its manifest/config
  hashes and proxy-only label. Never call it an official result.
- [ ] Run the full security/data-handling review and document unresolved
  multi-tenant, encryption, provider-egress, and backup limitations.
- [ ] For official evaluation, archive the platform Smoke/Full receipts and
  exact allowed-model/config/task hashes separately from synthetic reports.
- [ ] Update `CHANGELOG.md`, version metadata, release notes, and SBOM/image
  digest; create a signed tag only from a reviewed clean commit.
