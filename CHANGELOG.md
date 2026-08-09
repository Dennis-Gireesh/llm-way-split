# Changelog

This file records user-visible and trust-boundary changes for each immutable
release. Dates use UTC.

## 0.1.2 — 2026-08-09

Launcher correction: fresh installs now pull the password-free `v0.1.1`
image by default.

## 0.1.1 — 2026-08-09

Simplified trusted-local launch.

- Removed the browser password/unlock-token screen; the launcher opens the app
  directly on loopback.
- Added a visible first-run guide in the browser and a one-click Mac launcher.
- Kept legacy `WAYSPLIT_BROWSER_ACCESS_TOKEN` values harmlessly ignored for
  upgrades, while documenting that the local port is not authenticated.

## 0.1.0 — 2026-08-09

Initial public release.

- Added the complete local browser workflow: model discovery/readiness, generic
  statement upload, strict normalized-bill review, household ownership rules,
  deterministic preview, explicit confirmation, and local run history.
- Added carrier-neutral PDF/image ingestion with native text extraction, OCR and
  vision fallback, bounded resources, and local Ollama/OpenAI-compatible model
  adapters.
- Added exact decimal allocation, stable largest-remainder rounding, dual exact
  reconciliation, evidence/confidence gates, source/logical duplicate
  detection, and failed-extraction re-upload recovery.
- Added SQLite workflow state, single-use digest-bound confirmations, database
  integrity checks, and a verifiable hash-linked audit chain.
- Added the official Splitwise adapter with minimized account lookup, exact
  shares, no blind retry, read-back verification, explicit ambiguous state, and
  app-created expense rollback.
- Added a non-root, read-only Compose deployment on loopback port `9876`, optional
  internal Ollama, locked dependencies, pinned images, CI, CodeQL, Trivy gates,
  release SBOMs/provenance/checksums, and rollback controls.
- Added project-authored synthetic fixtures plus architecture, privacy, threat,
  security, contribution, conduct, release, and clean-room provenance policies.

WhatsApp, email ingestion, schedules, multi-user authentication, and unattended
posting are intentionally out of scope for this release.
