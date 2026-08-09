# Contributing

Thank you for helping build a bill splitter people can inspect and operate on their own hardware. Trust is part of the product: correctness, privacy, provenance, and safe failure matter as much as extraction quality.

By submitting a contribution, you agree that it may be distributed under the repository's Apache License 2.0 and that you have the right to submit it. You retain copyright in your contribution.

## Before you start

For a substantial feature, security-sensitive change, schema change, database migration, or new external integration, open an issue first. Describe the user problem, trust boundaries, failure behavior, and compatibility impact. This avoids spending time on a design that conflicts with the project's safety model.

Read these documents before changing a related area:

- `docs/ARCHITECTURE.md` for component and data-flow boundaries;
- `docs/THREAT_MODEL.md` for assets and required controls;
- `docs/PRIVACY.md` for data handling;
- `docs/PROVENANCE.md` before referring to another implementation; and
- `docs/RELEASES_AND_ROLLBACK.md` for migrations and release behavior.

Participation is governed by `CODE_OF_CONDUCT.md`. Report vulnerabilities according to `SECURITY.md`, not in a public issue.

## Non-negotiable design rules

- Keep statement processing local by default. Cloud model services must never be silently enabled.
- Use the carrier-agnostic normalized bill schema as the core contract. Carrier adapters may improve ingestion, validation, or edge-case handling; they must not become the only source of monetary truth.
- Treat documents, OCR text, and model output as untrusted input.
- Limit the model to extraction and classification. Monetary allocation, exact rounding, reconciliation, duplicate detection, and posting eligibility belong in deterministic code.
- Never make an external post unless extracted charges reconcile exactly to the statement total at the cent boundary.
- Show a dry-run preview and require explicit confirmation for external side effects. Confirmation must apply to the exact plan posted.
- Do not put credentials, real statements, personal data, or derived production data in Git, test fixtures, screenshots, issues, or CI logs.
- Preserve an auditable explanation of how each participant's amount was derived.
- Fail closed on ambiguous recipient mapping, duplicate status, changed inputs, or an uncertain API result.

## Development workflow

1. Create a focused branch from the current development branch.
2. Make the smallest coherent change and update documentation with the behavior.
3. Add tests at the boundary being changed. Monetary behavior requires exact expected amounts and reconciliation tests.
4. Run the repository's documented formatting, static analysis, test, secret-scanning, and dependency checks.
5. Review the diff for sensitive data and third-party material before opening a pull request.
6. Explain risk, validation, migration, and rollback in the pull request.

The repository's actual commands and supported tool versions are defined by its checked-in development configuration and CI workflow. Do not weaken or bypass a failing safety check to make a pull request pass.

## Test data

Use synthetic documents created for this project or clearly licensed reusable fixtures. Redaction is not automatically safe: PDFs and images may retain text layers, metadata, revisions, thumbnails, or embedded attachments. Prefer generating a synthetic statement from invented people, numbers, account identifiers, dates, and amounts.

Fixtures should cover:

- multi-page text PDFs and image-only documents;
- missing, duplicated, negative, prorated, taxed, and one-time charges;
- device installments and account-level charges;
- ambiguous ownership and intentionally low confidence;
- totals that reconcile and totals that deliberately do not;
- exact rounding boundaries and remainder distribution;
- repeated submissions and changed files with similar visible content; and
- external API success, rejection, timeout, retry, and indeterminate responses.

Never use a live Splitwise group or production credential in automated tests.

## Money and reconciliation changes

Represent money as decimal values in an explicit ISO 4217 currency. Do not introduce binary floating-point arithmetic into allocation or reconciliation. A pull request that changes allocation must state:

- the allocation invariant;
- the rounding unit and deterministic remainder rule;
- treatment of discounts, credits, taxes, fees, and negative amounts;
- behavior for missing owners or participants; and
- why the sum of participant amounts equals the normalized bill amount.

Tests must demonstrate that a reconciliation failure prevents posting, regardless of model confidence.

## Schema and adapter changes

Normalized schema changes require a versioning and migration assessment. Prefer additive, optional fields when semantics are unambiguous. Do not overload an existing field with carrier-specific meaning.

A carrier adapter may detect a carrier, improve text preparation, add validation hints, or normalize a documented edge case. It must emit the same generic schema and cannot bypass global validation or posting gates. Include carrier-neutral tests for the core behavior and synthetic carrier-shaped fixtures for the adapter behavior.

## External destinations

New destinations must implement preview, explicit confirmation, idempotency or duplicate defenses, minimal-data payloads, safe retry behavior, and remote-result recording. Document exactly what leaves the host and how an operator reverses a side effect. If the remote system cannot provide reliable idempotency, expose that limitation and fail closed after an indeterminate response.

## Dependencies and generated content

- Prefer a maintained dependency over a large custom security-sensitive implementation when its license and maintenance posture are acceptable.
- Pin direct dependencies and lock transitive dependencies using the repository's chosen tooling.
- Record the source and license of copied or adapted material. Do not copy from a repository with no compatible license.
- Do not submit model-generated code, prose, fixtures, or assets unless you have reviewed them for correctness, sensitive data, and potential third-party copying. The contributor remains responsible for provenance.
- Keep generated lockfiles and SBOM inputs reproducible; do not hand-edit generated files.

See `docs/PROVENANCE.md` for the reference-project policy.

## Pull request checklist

A pull request should answer:

- What user-visible behavior changes?
- Which trust boundary or failure mode is affected?
- Can the change cause an external side effect?
- What tests and manual checks were run?
- Does it alter stored data, configuration, network egress, or secrets?
- Is a migration required, and can the prior release read the new database?
- What is the rollback path?
- Was any third-party source, documentation, prompt, fixture, or asset used?

Maintainers may request changes or decline contributions that increase privacy or correctness risk without a proportionate user benefit.
