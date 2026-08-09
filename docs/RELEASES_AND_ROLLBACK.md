# Releases and Rollback

## Purpose and status

This document defines the release bar for production-supported versions and the operator procedure for returning to a known-good version. It does not imply that a stable release or every listed automation exists yet. A release is production-supported only when its release notes explicitly say so and provide the evidence described here.

## Versioning and channels

The project uses Semantic Versioning for tagged application releases:

- **major** versions may introduce incompatible schema, configuration, API, or operating changes;
- **minor** versions add backward-compatible behavior or opt-in capabilities; and
- **patch** versions contain backward-compatible fixes, including security fixes where feasible.

Pre-release versions such as alpha, beta, and release candidates are evaluation builds. The development branch is not a release and may contain incomplete migrations or controls.

Container images and source archives are published for immutable version tags. Operators should deploy a version or image digest, never a moving development tag. If a `latest` convenience tag exists, it is not sufficient for reproducible or rollback-safe deployments.

## Stable release gate

A stable release must have:

- a reviewed, clean source tree and an annotated version tag;
- pinned direct dependencies, a committed lock state, and immutable base-image references for the release build;
- passing unit, integration, schema, migration, allocation/property, reconciliation, duplicate, and destination contract tests;
- a test proving that reconciliation failure prevents posting;
- a test proving that changed plan content invalidates confirmation;
- a test of Splitwise success, rejection, timeout, indeterminate response, and duplicate/retry behavior without using live household data;
- static analysis, dependency review, secret scanning, and container/filesystem vulnerability scanning;
- an SBOM for distributed artifacts and published artifact checksums;
- synthetic/redacted-by-construction fixtures only, with a review for embedded metadata and text layers;
- an updated threat model, privacy document, provenance record, and third-party license inventory;
- human verification of the browser dry-run and explicit-confirmation path on supported Mac/Linux deployment targets;
- backup/restore and forward/rollback migration tests using synthetic data;
- release notes with known limitations, supported models/endpoints, data-retention behavior, external data sent, and upgrade/rollback instructions; and
- no critical known vulnerability in an exercised path unless the maintainers document an exceptional risk decision prominently. High-severity exceptions require the same treatment.

Passing a scanner is evidence, not proof of security. A model benchmark is evidence of extraction quality, not permission to bypass deterministic gates.

## Release artifacts

Each release should publish or link to:

- source archive and immutable commit/tag;
- container image digest for each supported architecture;
- checksums and, when the release infrastructure supports it, signatures/provenance attestations;
- SPDX or CycloneDX SBOM for application and container contents;
- third-party notices/licenses required by distributed dependencies;
- database schema and normalized-bill schema versions;
- migration compatibility table;
- configuration changes and safe example configuration;
- supported/verified OCR and model combinations with exact identifiers or digests where available; and
- release notes and security advisories relevant to upgrading.

Model files are normally obtained from their own distributor and are not implicitly covered by this repository's Apache-2.0 license. Operators must review model license, provenance, integrity, and resource requirements separately.

## Change and migration policy

Database and normalized-schema migrations are versioned, ordered, and tested from every supported upgrade source. A migration must declare whether it is:

- backward compatible with the prior application version;
- forward-only but reversible using a tested down migration;
- reversible only by restoring a pre-upgrade backup; or
- irreversible, in which case the release requires explicit operator acknowledgement and a tested export/recovery path.

Migrations must not silently discard original monetary values, confirmations, posting state, plan digests, remote IDs, or audit evidence. A corrected extraction creates a new version/run and preserves the relationship to the superseded result.

The application must refuse to start against an unsupported newer database rather than guessing compatibility. A preflight must check free space, database integrity, current schema, backup destination, configuration compatibility, and model/destination readiness before applying an upgrade.

In the `v0.1.0` alpha, repository initialization is also performed by server
startup, `waysplit doctor`, and `waysplit audit-verify`. Those commands are not
read-only database inspectors: when they encounter the supported legacy
pre-release schema, they can migrate it before returning diagnostics. The
legacy schema-2 migration first makes an online, integrity-checked,
permission-restricted copy named
`waysplit.pre-schema-2.<timestamp>.sqlite3` in the data directory. This local
safety copy does not replace step 5 below: before invoking any newer WaySplit
binary, make and verify a database-consistent backup in separately protected
storage. A database declaring a schema version newer than the application
supports is rejected instead of migrated.

## Operator upgrade procedure

Release notes may add version-specific steps, but a production upgrade follows this sequence:

1. Read the complete release notes, security advisories, compatibility table, privacy changes, and known limitations.
2. Finish or explicitly resolve every `posting` or `indeterminate` run. Do not upgrade while a remote result is unknown.
3. Stop new ingestion and posting.
4. Record the current application/image version, image digest, database schema version, configuration digest, and model identifier.
5. Create a protected, timestamped backup of SQLite using a database-consistent method, plus required non-secret configuration. Do not copy a live database file in a way that omits WAL state.
6. Verify the backup can be opened/restored and protect it as sensitive data.
7. Pull and verify the exact release artifact, checksum/signature where provided, and SBOM/scan result.
8. Apply the upgrade and migration, then run documented health and audit-chain checks.
9. Perform a synthetic dry run. Confirm that no external post occurs during the check.
10. Re-enable real ingestion only after local validation passes. Keep the prior image and backup until the operator's retention window closes.

## Rollback decision

Pause posting immediately when an upgrade causes any of the following:

- incorrect extraction or allocation compared with the preview/source;
- reconciliation or confirmation gates can be bypassed;
- duplicate detection, audit verification, or posting state becomes unreliable;
- database corruption or an unexplained migration result;
- unexpected network egress, sensitive logging, or credential exposure;
- inability to classify a Splitwise request as safe, failed, or indeterminate; or
- a security advisory affecting the deployed path.

Preserve diagnostic evidence without copying raw statements unnecessarily. If sensitive data or a credential may be exposed, follow `SECURITY.md` and rotate affected credentials before resuming.

## Application rollback procedure

1. Disable new ingestion and all posting. Keep the service unavailable rather than risk another side effect.
2. Record the failing version, database schema, last successful run, every confirmed/posting/indeterminate plan digest, and known Splitwise remote IDs.
3. Make a forensic backup of the current state before altering it. Restrict access and retention.
4. Consult the release compatibility table. If the prior application supports the current database, deploy its exact former digest without changing the database.
5. If the prior application does not support the migrated database, stop the service and restore the verified pre-upgrade database plus matching configuration. Never point the old application at an unsupported newer schema.
6. Start the prior version with posting disabled. Run database integrity, migration-version, audit-chain, and synthetic dry-run checks.
7. Reconcile external state manually: compare all intents since the backup with Splitwise expenses and record remote identifiers or indeterminate outcomes in the incident record.
8. Re-enable local processing first. Re-enable posting only after every ambiguous remote request is resolved and the operator reviews a new preview.

Do not merge the failing database and the restored database by copying rows manually. A project-provided, tested recovery tool or an explicitly reviewed incident procedure is required.

## External side effects are not rolled back with software

Reverting the container or restoring SQLite cannot remove or change an expense already created in Splitwise. Deleting/correcting a remote expense is a separate operator-authorized side effect. Before taking it, verify the remote expense ID, participants, amount, and whether household members have already acted on it. Record the correction in the local incident/audit history when the deployed version supports that operation.

If a request timed out after transmission, assume it may have succeeded. Search or query the destination using the stable plan identity and inspect the group before retrying. Never resolve uncertainty with an unconditional duplicate post.

Future WhatsApp delivery is similarly irreversible from the application's perspective: deleting a local event cannot recall a delivered message.

## Security releases

Security fixes follow the same artifact and migration controls, with disclosure details delayed when necessary to protect operators. Release notes state affected versions, impact, preconditions, remediation, credential or data-review steps, and whether a rollback is safer than an upgrade. The support window is defined in `SECURITY.md`.

## Release retention and recovery drills

Maintain enough immutable prior images and migration artifacts to execute the documented rollback for supported versions. Operators decide backup retention according to their privacy needs, but should periodically test restoration with non-production data. Maintainers should perform a synthetic recovery drill before a stable major release and after material database/posting-state changes.
