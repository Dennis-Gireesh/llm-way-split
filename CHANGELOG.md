# Changelog

This file records user-visible and trust-boundary changes for each immutable
release. Dates use UTC.

## 0.1.9 — 2026-08-09

Simplified Splitwise setup with a guided three-step personal API-key flow,
account/group/member discovery, and clearer privacy handling. The app now
explains why OAuth requires a registered Splitwise app in self-hosted mode.

## 0.1.8 — 2026-08-09

Added deterministic handling for service tables with per-line Total columns
and corrected prior-cycle payment context using the printed Total services and
Total due values. The supplied AT&T sample now reconciles at $414.84.

## 0.1.7 — 2026-08-09

Improved table extraction for mobile statements with per-line Total columns and
hid the WhatsApp summary until a valid deterministic preview exists.

## 0.1.6 — 2026-08-09

Blocked statements can now be safely re-uploaded for another local extraction
attempt. Active, completed, and posted statements remain duplicate-protected.

## 0.1.5 — 2026-08-09

Strengthened local bill extraction guidance to avoid counting subtotals and
grand totals as duplicate charges, and to self-check the signed bill equation.

## 0.1.4 — 2026-08-09

Added a deterministic, copy-ready WhatsApp bill summary to the extraction and
reconciliation step. It includes the billing cycle, total, and each person’s
allocated amount, and is never sent automatically.

## 0.1.3 — 2026-08-09

Raised the default statement page limit from 24 to 60 so ordinary multi-page
mobile bills can be processed without configuration changes.

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
